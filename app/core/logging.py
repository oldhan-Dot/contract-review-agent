import re
import sys

from loguru import logger

from app.core.config import ROOT, get_settings


def configure_logging() -> None:
    settings = get_settings()
    secrets = [
        value.get_secret_value()
        for value in (settings.mysql_password, settings.llm_api_key, settings.ocr_api_key)
        if value.get_secret_value()
    ]

    def redact(record):
        message = record["message"]
        for value in secrets:
            message = message.replace(value, "***")
        record["message"] = re.sub(r"sk-[\w-]+", "sk-***", message)
        return True

    (ROOT / "logs").mkdir(exist_ok=True)
    logger.remove()
    options = {"level": settings.log_level, "filter": redact, "diagnose": False}
    logger.add(sys.stderr, **options)
    logger.add(ROOT / "logs" / "app.log", rotation="10 MB", retention="7 days", **options)
