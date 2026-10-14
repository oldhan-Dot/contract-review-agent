from functools import lru_cache

from sqlalchemy import URL, create_engine
from sqlalchemy.engine import Engine

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    url = URL.create(
        "mysql+pymysql",
        username=settings.mysql_user,
        password=settings.mysql_password.get_secret_value(),
        host=settings.mysql_host,
        port=settings.mysql_port,
        database=settings.mysql_database,
        query={"charset": "utf8mb4"},
    )
    return create_engine(
        url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5, "read_timeout": 10, "write_timeout": 10},
    )
