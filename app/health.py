import asyncio

import httpx
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import get_engine


def check_db() -> dict:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
            tables = connection.execute(text("SHOW TABLES")).scalars().all()
            users = connection.scalar(text("SELECT COUNT(*) FROM app_user"))
        return {"status": "ok", "tables": len(tables), "users": users}
    except Exception:
        return {"status": "error", "message": "数据库连接或初始化失败，请运行 init-db"}


async def check_llm() -> dict:
    settings = get_settings()
    if not settings.llm_enabled:
        return {"status": "disabled", "message": "待配置模型服务，当前仅运行环境骨架"}
    if not settings.llm_api_key.get_secret_value():
        return {"status": "error", "message": "尚未配置 LLM_API_KEY"}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"{settings.llm_base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {settings.llm_api_key.get_secret_value()}"},
            )
        if response.status_code in (404, 405):
            return {"status": "unverified", "message": "服务未提供 /models，需单独验证推理接口"}
        if response.status_code != 200:
            return {"status": "error", "message": f"模型服务返回 HTTP {response.status_code}"}
        model_ids = {item.get("id") for item in response.json().get("data", [])}
        if settings.llm_model_name not in model_ids:
            return {"status": "error", "message": "配置的模型名不在 /models 列表中"}
        return {"status": "reachable", "message": "模型列表及认证可用，推理调用待验证"}
    except Exception:
        return {"status": "error", "message": "模型服务检查失败，请核对地址与认证配置"}


async def check_mineru() -> dict:
    settings = get_settings()
    if not settings.mineru_enabled:
        return {"status": "disabled", "message": "待配置 MinerU 文档解析服务"}
    if not settings.mineru_api_token.get_secret_value():
        return {"status": "error", "message": "尚未配置 MINERU_API_TOKEN"}
    if not settings.mineru_health_path:
        return {
            "status": "unverified",
            "message": "MinerU 地址和 Token 已配置，解析接口待接入验证",
        }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                settings.mineru_base_url.rstrip("/")
                + "/"
                + settings.mineru_health_path.lstrip("/"),
                headers={"Authorization": f"Bearer {settings.mineru_api_token.get_secret_value()}"},
            )
        if response.status_code == 200:
            return {"status": "reachable", "message": "健康端点可达，MinerU 解析接口待验证"}
        return {"status": "error", "message": f"MinerU 服务返回 HTTP {response.status_code}"}
    except Exception:
        return {"status": "error", "message": "MinerU 服务连接失败，请核对地址"}


async def health_report() -> dict:
    db, llm, mineru = await asyncio.gather(asyncio.to_thread(check_db), check_llm(), check_mineru())
    return {
        "status": "ok" if db["status"] == "ok" else "error",
        "phase": "environment-setup",
        "db": db,
        "llm": llm,
        "mineru": mineru,
        "approval": {"status": "pending", "message": "Mock 审批业务接口尚未实现"},
    }
