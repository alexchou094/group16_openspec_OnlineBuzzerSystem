import os
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from google_auth_oauthlib.flow import Flow
from pydantic import BaseModel, StrictInt
from starlette.concurrency import run_in_threadpool

from app.adapters.guided_notes import GoogleWorksheetReader, SheetAccessError
from app.domain.activity import Phase
from app.repositories.memory import Session
from app.services.activities import ActivityError, ActivityService
from app.services.guided_notes import GuidedNotesImport
from app.services.result_export import ResultExport
from app.web.google_oauth import OAUTH_WINDOW, exchange_code, start_flow
from app.web.result_export import register_export_routes


CALLBACK = "/google/oauth/callback"
OAUTH_COOKIE = "buzzer_google_oauth"


@dataclass(frozen=True)
class GoogleSettings:
    client_file: str
    picker_key: str
    project_number: str

    @classmethod
    def from_environment(cls):
        settings = cls(
            os.getenv("BUZZER_GOOGLE_OAUTH_CLIENT_FILE", ""),
            os.getenv("BUZZER_GOOGLE_PICKER_KEY", ""),
            os.getenv("BUZZER_GOOGLE_PROJECT_NUMBER", ""),
        )
        if not (Path(settings.client_file).is_file() and settings.picker_key
                and re.fullmatch(r"[0-9]+", settings.project_number)):
            raise ActivityError(503, "請依操作文件設定 Google OAuth 與 Picker。")
        return settings


@dataclass
class Authorization:
    activity_id: str
    session_id: str
    state: str
    browser_token: str
    valid_until: float
    flow: Flow | None
    page: str = "import"
    grant: tuple[str, datetime] | None = None
    message: str = ""


class Selection(BaseModel):
    spreadsheet_id: str
    sheet_id: StrictInt | None = None


def register_import_routes(
    app: FastAPI, templates: Jinja2Templates, service: ActivityService,
    existing_session: Callable[[Request], Session], authorized_mutation: Callable[[Request], Session],
) -> None:
    importer = GuidedNotesImport(service)
    exporter = ResultExport(service)
    authorizations: dict[str, Authorization] = {}
    Mutation = Annotated[Session, Depends(authorized_mutation)]

    def export_writer(activity_id: str):
        authorization = authorizations.get(activity_id)
        grant = authorization.grant if authorization else None

        def writer():
            if grant is None:
                raise SheetAccessError(401, "請先授權 Google 再試一次。")
            return GoogleWorksheetReader(*grant)

        return writer

    register_export_routes(app, templates, exporter, existing_session, authorized_mutation, export_writer)

    @app.exception_handler(SheetAccessError)
    async def sheet_error(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=error.status)

    def google_reader(activity_id: str) -> GoogleWorksheetReader:
        authorization = authorizations.get(activity_id)
        if authorization is None or authorization.grant is None:
            raise ActivityError(401, "請先授權 Google。")
        reader = GoogleWorksheetReader(*authorization.grant)
        try:
            reader.check_expiry()
        except SheetAccessError:
            del authorizations[activity_id]
            raise
        return reader

    def prune_authorizations():
        now = monotonic()
        for activity_id, authorization in list(authorizations.items()):
            if authorization.grant is None:
                if authorization.valid_until <= now:
                    del authorizations[activity_id]
            else:
                try:
                    GoogleWorksheetReader(*authorization.grant).check_expiry()
                except SheetAccessError:
                    del authorizations[activity_id]

    @app.get("/activities/{activity_id}/import", include_in_schema=False)
    async def import_page(activity_id: str, request: Request):
        session = existing_session(request)
        state = service.state(activity_id, session.id)
        if state["role"] != "teacher":
            raise ActivityError(403, "只有活動老師可以匯入學習單。")
        authorization = authorizations.get(activity_id)
        summary = state.get("import_summary")
        return templates.TemplateResponse(request=request, name="guided_notes_import.html", context={
            "activity_id": activity_id, "csrf": session.csrf, "summary": summary,
            "preparing": state["phase"] == Phase.PREPARING,
            "authorized": bool(authorization and authorization.grant),
            "message": authorization.message if authorization else "",
        })

    @app.post("/activities/{activity_id}/import/oauth")
    async def authorize(activity_id: str, session: Mutation, request: Request):
        importer.check_preparing(activity_id, session.id)
        return begin_authorization(activity_id, session, request, "import")

    @app.post("/activities/{activity_id}/exports/oauth")
    async def authorize_export(activity_id: str, session: Mutation, request: Request):
        with exporter.locked(activity_id, session.id):
            pass
        return begin_authorization(activity_id, session, request, "exports")

    def begin_authorization(activity_id: str, session: Session, request: Request, page: str):
        settings = GoogleSettings.from_environment()
        origin = f"{request.url.scheme}://{request.url.netloc}"
        try:
            flow, url, state = start_flow(settings.client_file, origin + CALLBACK)
        except (OSError, ValueError):
            raise ActivityError(503, "無法載入 Google OAuth Web 用戶端設定。") from None
        prune_authorizations()
        browser_token = secrets.token_urlsafe(32)
        authorizations[activity_id] = Authorization(
            activity_id, session.id, state, browser_token, monotonic() + OAUTH_WINDOW, flow, page,
        )
        response = JSONResponse({"url": url})
        response.set_cookie(OAUTH_COOKIE, browser_token, max_age=OAUTH_WINDOW, httponly=True,
                            samesite="lax", secure=request.url.scheme == "https", path=CALLBACK)
        return response

    @app.get(CALLBACK, include_in_schema=False)
    async def oauth_callback(request: Request):
        supplied_state = request.query_params.get("state", "")
        browser_token = request.cookies.get(OAUTH_COOKIE, "")
        authorization = next((item for item in authorizations.values()
                              if item.browser_token == browser_token and item.flow is not None), None)
        if (authorization is None or not supplied_state.isascii()
                or not secrets.compare_digest(supplied_state, authorization.state)):
            raise ActivityError(400, "無法驗證 Google 授權請求，請回匯入頁重新授權。")
        activity_id = authorization.activity_id
        def check_authorization():
            if authorization.page == "import":
                importer.check_preparing(activity_id, authorization.session_id)
            else:
                with exporter.locked(activity_id, authorization.session_id):
                    pass

        check_authorization()
        flow, authorization.flow = authorization.flow, None
        code = request.query_params.get("code")
        if monotonic() >= authorization.valid_until:
            authorization.message = "Google 授權請求已過期，請重新授權。"
        elif request.query_params.get("error") == "access_denied":
            authorization.message = "Google 授權已拒絕或取消。"
        elif not code or "error" in request.query_params:
            authorization.message = "Google 授權未完成，請重新授權。"
        else:
            grant = await run_in_threadpool(exchange_code, flow, code)
            check_authorization()
            authorization.grant = grant
            authorization.message = ("已授權 Google，可以選擇試算表。" if grant
                                     else "Google 授權交換失敗，請重新授權並確認設定。")
        # 先完成同站頁面導覽，再返回需要 Strict Cookie 的老師頁面。
        response = templates.TemplateResponse(request=request, name="google_oauth_return.html",
                                              context={"activity_id": activity_id, "page": authorization.page})
        response.delete_cookie(OAUTH_COOKIE, path=CALLBACK)
        return response

    @app.post("/activities/{activity_id}/import/picker")
    async def picker(activity_id: str, session: Mutation):
        importer.check_preparing(activity_id, session.id)
        reader = google_reader(activity_id)
        settings = GoogleSettings.from_environment()
        return {"token": reader.token, "key": settings.picker_key, "project": settings.project_number}

    @app.post("/activities/{activity_id}/import/select")
    async def select(activity_id: str, session: Mutation, body: Selection):
        importer.check_preparing(activity_id, session.id)
        return await run_in_threadpool(
            importer.select, activity_id, session.id, google_reader(activity_id), body.spreadsheet_id, body.sheet_id,
        )
