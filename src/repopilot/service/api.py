from contextlib import asynccontextmanager
from hmac import compare_digest
from time import perf_counter
from uuid import UUID, uuid4

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.exceptions import HTTPException as StarletteHTTPException
from repopilot.service.metrics import Metrics
from repopilot.service.bounds import BodyLimit

from repopilot.service.logging import event, configure
from repopilot.service.schemas import TaskCreate, TaskView, ResultView, TraceView, ErrorView
from repopilot.service.settings import Settings
from repopilot.service.store import Store, IdempotencyConflict, AdmissionFull


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = Store(settings, pool_size=8)
    metrics = Metrics()
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            store.close()
    app = FastAPI(title="RepoPilot task service", version="1.0.0", lifespan=lifespan, responses={code:{"model":ErrorView} for code in (401,404,409,413,422,429,500,503)})
    app.state.store = store
    app.state.metrics = metrics
    app.add_middleware(BodyLimit)

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        try:
            request_id = UUID(request.headers.get("X-Request-ID", ""))
        except ValueError:
            request_id = uuid4()
        request.state.request_id = request_id
        started = perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = str(request_id)
        event("http_request", request_id=request_id, task_id=getattr(request.state, "task_id", None),
              method=request.method, status=response.status_code,
              duration_ms=round((perf_counter() - started) * 1000, 2))
        route = request.scope.get('route')
        metrics.request(request.method, getattr(route,'path','unmatched'), response.status_code, perf_counter()-started)
        return response

    def error(request, status, code, detail, headers=None):
        return JSONResponse(status_code=status, content={'detail':detail,'code':code,
            'request_id':str(request.state.request_id)}, headers=headers)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        codes = {401:'authentication_required',404:'not_found',409:'state_conflict',422:'invalid_request',429:'admission_full',503:'unavailable'}
        return error(request, exc.status_code, codes.get(exc.status_code,'http_error'),str(exc.detail),exc.headers)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        event('internal_error', request_id=request.state.request_id)
        return error(request,500,'internal_error','internal server error',{'X-Request-ID':str(request.state.request_id)})

    @app.exception_handler(psycopg.Error)
    async def database_error(request, exc):
        event("database_unavailable", request_id=request.state.request_id)
        return error(request,503,"database_unavailable","database unavailable",{"Retry-After":"1"})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # FastAPI's default validation response echoes input, possibly including secrets.
        return error(request,422,"invalid_request","invalid request")

    bearer = HTTPBearer(auto_error=False)
    def authenticate(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None or not compare_digest(credentials.credentials.encode(),settings.api_token.encode()):
            raise HTTPException(401,"authentication required",headers={'WWW-Authenticate':'Bearer'})

    @app.get('/metrics', response_class=PlainTextResponse, dependencies=[Depends(authenticate)])
    def metric_snapshot():
        return PlainTextResponse(metrics.render(store),media_type='text/plain; version=0.0.4; charset=utf-8')

    def task_for(task_id: UUID, request: Request):
        request.state.task_id = task_id
        task = store.get(task_id)
        if task is None:
            raise HTTPException(404, "task not found")
        return task

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/readyz")
    def ready():
        if not store.ready():
            raise HTTPException(503, "database migration required")
        return {"status": "ready", "execution_mode": "scripted" if settings.scripted else "provider"}

    @app.post("/v1/tasks", response_model=TaskView, status_code=202, dependencies=[Depends(authenticate)])
    def create(payload: TaskCreate, request: Request, response: Response,
               idempotency_key: str | None = Header(default=None, min_length=1, max_length=200)):
        data = payload.model_dump()
        if any(secret in value for secret in settings.secrets for value in (payload.issue, payload.repository)):
            raise HTTPException(422, "credentials are not accepted in task content")
        # Validate only on first creation so a replay remains possible after the source is removed.
        if idempotency_key is not None and not idempotency_key.strip():
            raise HTTPException(422, "invalid idempotency key")
        try:
            task, created = store.submit(data, idempotency_key, request.state.request_id)
        except AdmissionFull:
            metrics.event("rejected")
            raise HTTPException(429,"in-flight task capacity reached",headers={"Retry-After":"1"}) from None
        except IdempotencyConflict:
            metrics.event("conflict")
            raise HTTPException(409, "idempotency key conflicts with an existing request") from None
        except (ValueError, OSError):
            raise HTTPException(422, "repository must exist under the approved workspace root") from None
        metrics.event("created" if created else "deduplicated")
        request.state.task_id = task["id"]
        response.status_code = 202 if created else 200
        response.headers["Location"] = f"/v1/tasks/{task['id']}"
        if created:
            # INSERT RETURNING is the committed creation snapshot. A second
            # connection/read adds load and can fail after admission succeeded.
            # Return that internally consistent snapshot even if a worker has
            # already claimed it; clients obtain newer state through GET.
            return {**task, "runs": []}
        return store.get(task["id"])

    @app.get("/v1/tasks/{task_id}", response_model=TaskView, dependencies=[Depends(authenticate)])
    def status(task_id: UUID, request: Request):
        return task_for(task_id, request)

    @app.get("/v1/tasks/{task_id}/result", response_model=ResultView, dependencies=[Depends(authenticate)])
    def result(task_id: UUID, request: Request):
        task = task_for(task_id, request)
        if task["status"] not in {"SUCCEEDED", "FAILED"}:
            raise HTTPException(409, "task has no terminal result yet")
        run = task["runs"][-1]
        return dict(task_id=task_id, run_id=run["id"], status=task["status"], stop_reason=run["stop_reason"],
                    error_code=run["error_code"], result=run["result"])

    @app.get("/v1/tasks/{task_id}/trace", response_model=TraceView, dependencies=[Depends(authenticate)])
    def trace(task_id: UUID, request: Request):
        task = task_for(task_id, request)
        # Safe metadata projection; no filesystem paths supplied by clients are opened.
        return {"task_id": task_id, "runs": task["runs"],
                "summaries": {str(run["id"]): run["result"]["trace_summary"] for run in task["runs"]
                              if run["result"] and "trace_summary" in run["result"]}}

    return app


def app_factory():
    configure()
    return create_app()
