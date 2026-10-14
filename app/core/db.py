from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import URL, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    """进程共享一个连接池；导入模块时不会立即连接数据库。"""
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


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """缓存会话工厂，每次调用工厂仍会创建独立的 Session。"""
    return sessionmaker(
        bind=get_engine(),
        # 禁止查询前隐式 flush，业务代码在需要写入时明确调用 flush/commit。
        autoflush=False,
        # 提交后仍可读取已加载字段，方便接口构建响应；最新值需 refresh。
        expire_on_commit=False,
    )


def get_db() -> Iterator[Session]:
    """供 FastAPI Depends 使用：提供会话，异常时回滚，结束时关闭。

    此依赖不自动提交。业务模块必须通过 session.commit() 或
    with session.begin() 明确划定事务，避免请求成功就误保存全部修改。
    """
    with get_session_factory()() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        # Session 上下文退出时会关闭会话，并回滚尚未提交的事务。
