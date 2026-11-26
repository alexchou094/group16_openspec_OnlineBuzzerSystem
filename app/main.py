import asyncio
import os
import secrets
from contextlib import asynccontextmanager, suppress
from hashlib import sha256
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, StrictBool, StrictInt, ConfigDict
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.domain.activity import Blank, Question, SourceSnapshot, ReviewStatus
from app.repositories.memory import MemoryRepository, Session
from app.services.activities import ActivityError, ActivityService
from app.web.guided_notes import CALLBACK as GOOGLE_CALLBACK, register_import_routes

ROOT = Path(__file__).parent
COOKIE = "buzzer_session"
COOKIE_MAX_AGE = 400 * 24 * 60 * 60


class CreateBody(BaseModel):
    group_limit: StrictInt


class JoinBody(BaseModel):
    code: str
    group_number: StrictInt
    credential: str | None = None


class OpenRoundBody(BaseModel):
    question_ids: list[str] | None = None
    fairness_enabled: StrictBool = False
    seconds: StrictInt | None = None


class FinalizeBody(BaseModel):
    expected_review_version: StrictInt


class SubmissionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str
    answers: list[str]


class ReviewBody(FinalizeBody):
    model_config = ConfigDict(extra="forbid")
    review_status: ReviewStatus


def create_app(service: ActivityService | None = None, *, demo: bool = False,
               hosts: tuple[str, ...] = ("localhost", "127.0.0.1")) -> FastAPI:
    service = service if service is not None else ActivityService(MemoryRepository())
    repository = service.repository

    async def check_deadlines():
        while True:
            await run_in_threadpool(service.expire_due_rounds)
            await asyncio.sleep(0.1)

    @asynccontextmanager
    async def lifespan(_app):
        task = asyncio.create_task(check_deadlines())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="課堂搶答系統", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(hosts))
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "templates")
    activity_script_version = sha256((ROOT / "static" / "activity.js").read_bytes()).hexdigest()[:12]

    @app.middleware("http")
    async def prevent_private_caching(request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        if request.url.path == GOOGLE_CALLBACK:
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(ActivityError)
    async def activity_error(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, error):
        messages = {"group_limit": "組數上限須為正整數。", "group_number": "組號須為整數。",
                    "seconds": "倒數須為 1 到 3600 的整數秒。"}
        fields = {item["loc"][-1] for item in error.errors() if item["loc"]}
        detail = next((message for field, message in messages.items() if field in fields), "請確認輸入資料格式。")
        return JSONResponse({"detail": detail}, status_code=422)

    def existing_session(request: Request) -> Session:
        session = repository.session(request.cookies.get(COOKIE))
        if session is None:
            raise ActivityError(401, "工作階段已失效，請回到首頁重新加入。")
        return session

    def authorized_mutation(request: Request) -> Session:
        session = existing_session(request)
        origin = f"{request.url.scheme}://{request.url.netloc}"
        token = request.headers.get("X-CSRF-Token", "")
        if request.headers.get("Origin") != origin or not token.isascii() or not secrets.compare_digest(token, session.csrf):
            raise ActivityError(403, "無法驗證請求來源，請重新整理頁面。")
        return session

    Mutation = Annotated[Session, Depends(authorized_mutation)]
    register_import_routes(app, templates, service, existing_session, authorized_mutation)

    def render(request: Request, activity_id: str = ""):
        return templates.TemplateResponse(request=request, name="index.html",
                                          context={"activity_id": activity_id, "demo": demo,
                                                   "activity_script_version": activity_script_version})

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        return render(request)

    @app.get("/activities/{activity_id}", include_in_schema=False)
    def activity_page(request: Request, activity_id: str):
        return render(request, activity_id)

    @app.get("/api/session")
    def session_info(request: Request, response: Response):
        session = repository.session(request.cookies.get(COOKIE))
        if session is None:
            session = repository.new_session()
        response.set_cookie(COOKIE, session.id, max_age=COOKIE_MAX_AGE, httponly=True,
                            samesite="strict", secure=request.url.scheme == "https")
        return {
            "csrf_token": session.csrf,
            "active_activity": repository.active_activity_id(session.id),
            "student_activities": service.student_activities(session.id),
        }

    @app.post("/activities", status_code=201)
    def create(body: CreateBody, session: Mutation):
        activity = service.create(session.id, body.group_limit)
        return {"id": activity.id, "code": activity.code}

    @app.post("/activities/join")
    def join(body: JoinBody, session: Mutation):
        activity_id = service.join(session.id, body.code, body.group_number, body.credential)
        return {"id": activity_id}

    @app.get("/activities/{activity_id}/state")
    def state(activity_id: str, request: Request):
        if repository.get(activity_id) is None:
            raise ActivityError(404, "活動已失效或不存在。")
        return service.state(activity_id, existing_session(request).id)

    @app.post("/activities/{activity_id}/leave", status_code=204)
    def leave(activity_id: str, session: Mutation):
        service.leave(activity_id, session.id)

    @app.delete("/activities/{activity_id}/groups/{number}", status_code=204)
    def delete_group(activity_id: str, number: int, session: Mutation):
        service.delete_group(activity_id, session.id, number)

    @app.post("/activities/{activity_id}/rounds", status_code=201)
    def open_round(activity_id: str, body: OpenRoundBody, session: Mutation):
        return {"id": service.open_round(activity_id, session.id, body.question_ids, body.fairness_enabled, body.seconds)}

    @app.post("/activities/{activity_id}/rounds/{round_id}/close")
    def close_round(activity_id: str, round_id: str, session: Mutation):
        return {"review_version": service.close_round(activity_id, session.id, round_id)}

    @app.post("/activities/{activity_id}/rounds/{round_id}/submissions")
    def submit(activity_id: str, round_id: str, body: SubmissionBody, session: Mutation):
        return service.submit(activity_id, session.id, round_id, body.question_id, body.answers)

    @app.patch("/activities/{activity_id}/rounds/{round_id}/reviews/{submission_id}")
    def review(activity_id: str, round_id: str, submission_id: str, body: ReviewBody, session: Mutation):
        return {"review_version": service.review(activity_id, session.id, round_id, submission_id,
                                                body.review_status, body.expected_review_version)}

    @app.post("/activities/{activity_id}/rounds/{round_id}/finalize", status_code=204)
    def finalize(activity_id: str, round_id: str, body: FinalizeBody, session: Mutation):
        service.finalize(activity_id, session.id, round_id, body.expected_review_version)

    @app.post("/activities/{activity_id}/cancel", status_code=204)
    def cancel(activity_id: str, session: Mutation):
        service.cancel(activity_id, session.id)

    @app.post("/activities/{activity_id}/finish", status_code=204)
    def finish(activity_id: str, session: Mutation):
        service.finish(activity_id, session.id)

    if demo:
        @app.post("/activities/{activity_id}/demo-questions", status_code=204)
        def demo_questions(activity_id: str, session: Mutation):
            questions = tuple(Question(f"demo-{n}", n, (Blank("1", f"B{n + 1}"),)) for n in range(1, 4))
            service.replace_questions(activity_id, session.id, SourceSnapshot("demo", 0, "示範題目", "demo"), questions)

    return app


app = create_app(demo=os.getenv("BUZZER_DEMO") == "1")
