import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from google_auth_oauthlib.flow import Flow
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.adapters.google_probe import GoogleSheetsProbe, ProbeError
from app.web.google_oauth import OAUTH_WINDOW, exchange_code, start_flow


ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:8001"
CALLBACK = ORIGIN + "/oauth/callback"
COOKIE = "buzzer_google_probe"


@dataclass(frozen=True)
class Config:
    client_file: str
    picker_key: str
    project_number: str
    spreadsheet_id: str

    @classmethod
    def from_environment(cls):
        config = cls(
            os.getenv("BUZZER_GOOGLE_OAUTH_CLIENT_FILE", ""),
            os.getenv("BUZZER_GOOGLE_PICKER_KEY", ""),
            os.getenv("BUZZER_GOOGLE_PROJECT_NUMBER", ""),
            os.getenv("BUZZER_GOOGLE_TEST_SPREADSHEET_ID", ""),
        )
        if not (Path(config.client_file).is_file() and config.picker_key
                and re.fullmatch(r"[0-9]+", config.project_number)
                and re.fullmatch(r"[A-Za-z0-9_-]+", config.spreadsheet_id)):
            raise ProbeError(503, "請依測試文件設定 OAuth 憑證、Picker 及測試複本。")
        return config


@dataclass
class Session:
    csrf: str
    valid_until: float
    oauth_state: str = ""
    pending: tuple[str, Flow, float] | None = None
    authorization: tuple[str, datetime] | None = None
    selected: bool = False
    message: str = ""


class Selection(BaseModel):
    spreadsheet_id: str


def create_app(settings: Config | None = None) -> FastAPI:
    app = FastAPI(title="Google OAuth／Picker 驗證")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1"])
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "templates")
    sessions: dict[str, Session] = {}

    def config() -> Config:
        return settings if settings is not None else Config.from_environment()

    def session_for(request: Request) -> Session | None:
        session = sessions.get(request.cookies.get(COOKIE, ""))
        return session if session and session.valid_until > monotonic() else None

    def authorized(request: Request) -> Session:
        session = session_for(request)
        csrf = request.headers.get("X-CSRF-Token", "")
        if (session is None or request.headers.get("Origin") != ORIGIN
                or not csrf.isascii() or not secrets.compare_digest(csrf, session.csrf)):
            raise ProbeError(403, "無法驗證本機工作階段，請重新整理頁面。")
        return session

    def sheets(session: Session, config: Config) -> GoogleSheetsProbe:
        if session.authorization is None:
            raise ProbeError(401, "請先授權 Google。")
        token, expiry = session.authorization
        return GoogleSheetsProbe(config.spreadsheet_id, token, expiry)

    @app.middleware("http")
    async def private_response(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = (
            "no-referrer" if request.url.path == "/oauth/callback" else "strict-origin-when-cross-origin"
        )
        return response

    @app.exception_handler(ProbeError)
    async def probe_error(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=error.status)

    @app.get("/")
    async def index(request: Request):
        now = monotonic()
        for key in [key for key, session in sessions.items() if session.valid_until <= now]:
            del sessions[key]
        key = request.cookies.get(COOKIE, "")
        session = sessions.get(key)
        if session is None:
            key = secrets.token_urlsafe(32)
            session = sessions[key] = Session(secrets.token_urlsafe(32), now + 7200)
        response = templates.TemplateResponse(request=request, name="google_probe.html", context={
            "csrf": session.csrf,
            "message": session.message,
            "authorized": session.authorization is not None,
            "selected": session.selected,
        })
        response.set_cookie(COOKIE, key, httponly=True, samesite="lax", max_age=7200)
        return response

    @app.post("/oauth/start")
    async def start(request: Request):
        session = authorized(request)
        try:
            flow, url, state = start_flow(config().client_file, CALLBACK)
        except (OSError, ValueError):
            raise ProbeError(503, "無法載入 Google OAuth Web 用戶端設定。") from None
        session.oauth_state = state
        session.pending = (state, flow, monotonic() + OAUTH_WINDOW)
        session.authorization = None
        session.selected = False
        session.message = ""
        return {"url": url}

    @app.get("/oauth/callback")
    async def callback(request: Request):
        session = session_for(request)
        supplied_state = request.query_params.get("state", "")
        if session is None or session.pending is None:
            raise ProbeError(400, "找不到授權請求，請回驗證頁重新授權。")
        state, flow, deadline = session.pending
        if not supplied_state.isascii() or not secrets.compare_digest(supplied_state, state):
            raise ProbeError(400, "無法驗證 Google 授權請求，請回驗證頁重新授權。")
        session.pending = None
        code = request.query_params.get("code")
        if monotonic() >= deadline:
            session.message = "Google 授權請求已過期，請重新授權。"
        elif request.query_params.get("error") == "access_denied":
            session.message = "Google 授權已拒絕或取消。"
        elif not code or "error" in request.query_params:
            session.message = "Google 授權未完成，請重新授權。"
        else:
            authorization = await run_in_threadpool(exchange_code, flow, code)
            message = ("Google 授權成功，請選擇測試複本。" if authorization
                       else "Google 授權交換失敗，請重新授權並確認設定。")
            if session.oauth_state == state:
                session.authorization = authorization
                session.message = message
        return RedirectResponse("/", status_code=303)

    @app.post("/picker")
    async def picker(request: Request):
        session = authorized(request)
        settings = config()
        probe = sheets(session, settings)
        probe.check_expiry()
        return {"token": probe.token, "key": settings.picker_key, "project": settings.project_number}

    @app.post("/select")
    async def select(request: Request, body: Selection):
        session = authorized(request)
        session.selected = False
        settings = config()
        if body.spreadsheet_id != settings.spreadsheet_id:
            raise ProbeError(403, "請選擇設定指定的測試複本。")
        authorization = session.authorization
        result = await run_in_threadpool(sheets(session, settings).read)
        if session.authorization is not authorization:
            raise ProbeError(401, "Google 授權已變更，請重新選檔。")
        session.selected = True
        return result

    @app.post("/read")
    @app.post("/write")
    async def verify(request: Request):
        session = authorized(request)
        if not session.selected:
            raise ProbeError(409, "請先用 Google Picker 選擇測試複本。")
        probe = sheets(session, config())
        operation = probe.write_and_verify if request.url.path == "/write" else probe.read
        return await run_in_threadpool(operation)

    return app


app = create_app()
