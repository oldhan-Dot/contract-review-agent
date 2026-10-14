"""真实数据库测试需要显式开启，避免默认测试依赖本机 MySQL。"""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from sqlalchemy.orm import sessionmaker

from app.api.tasks import router as tasks_router
from app.api.users import router as users_router
from app.core import db
from app.core.errors import register_error_handlers
from app.core.security import require_roles, require_task_access
from app.models import AppUser, ContractTask


def pytest_addoption(parser):
    parser.addoption("--run-db-tests", action="store_true", help="运行本机 MySQL 集成测试")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-db-tests"):
        return
    skip = pytest.mark.skip(reason="加 --run-db-tests 才运行真实 MySQL 测试")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def factory():
    """真实数据库测试共用的会话工厂，结束时回滚全部测试记录。"""
    with db.get_engine().connect() as connection:
        transaction = connection.begin()
        session_factory = sessionmaker(
            bind=connection,
            join_transaction_mode="create_savepoint",
            autoflush=False,
            expire_on_commit=False,
        )
        try:
            yield session_factory
        finally:
            transaction.rollback()


@pytest.fixture
def api_app():
    # 测试路由与异常处理，不启动真实应用的生命周期或后台服务。
    application = FastAPI()
    register_error_handlers(application)
    application.include_router(users_router)
    application.include_router(tasks_router)

    # 以下两个接口只存在于测试应用，用来验证可复用的权限依赖。
    @application.get("/test/tasks/{task_id}")
    def task_probe(task: Annotated[ContractTask, Depends(require_task_access)]):
        return {"id": task.id, "applicant_id": task.applicant_id}

    @application.get("/test/legal-only")
    def role_probe(user: Annotated[AppUser, Depends(require_roles("LEGAL"))]):
        return {"id": user.id}

    return application
