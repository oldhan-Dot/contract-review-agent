from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.db import get_engine
from app.core.logging import configure_logging
from app.health import check_db, health_report


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
    logger.info("基础环境启动：MySQL 可用，业务审查模块待实现")
    try:
        yield
    finally:
        get_engine().dispose()


app = FastAPI(title="合同审批审查系统 · 环境骨架", version="0.1.0", lifespan=lifespan)


@app.get("/api/health", tags=["环境检查"])
async def health():
    report = await health_report()
    return JSONResponse(report, status_code=200 if report["status"] == "ok" else 503)
