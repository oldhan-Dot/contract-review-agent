from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.concurrency import run_in_threadpool

from app.api.tasks import router as tasks_router
from app.api.users import router as users_router
from app.api.workspace import router as workspace_router
from app.core.config import get_settings
from app.core.db import get_engine
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.health import check_db, health_report
from app.services.runner import prepare_workspace_schema, runner


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.validate_startup()
    configure_logging()
    for name in ("uploads", "renders", "samples"):
        (settings.storage_path / name).mkdir(parents=True, exist_ok=True)
    database = await run_in_threadpool(check_db)
    if database["status"] != "ok":
        raise RuntimeError(database["message"])
    await run_in_threadpool(prepare_workspace_schema)
    runner.start()
    logger.info("合同审查工作台启动：后台执行器领取 pending 任务")
    try:
        yield
    finally:
        runner.stop()
        get_engine().dispose()


app = FastAPI(title="合同审批审查工作台", version="0.2.0", lifespan=lifespan)
register_error_handlers(app)
app.include_router(users_router)
app.include_router(tasks_router)
app.include_router(workspace_router)


@app.get("/api/health", tags=["环境检查"])
async def health():
    report = await health_report()
    return JSONResponse(report, status_code=200 if report["status"] == "ok" else 503)
