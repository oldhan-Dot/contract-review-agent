"""使用真实 MySQL 验证映射、默认值、幂等约束和会话回滚。"""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core import db
from app.models import AppUser, ContractTask

pytestmark = pytest.mark.integration


@pytest.fixture
def factory():
    # 外层事务最终回滚；Session 的 commit 只释放内层 SAVEPOINT。
    # 因此能验证提交行为，同时不把测试用户、任务留在业务库中。
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


def make_user() -> AppUser:
    return AppUser(username=f"test-{uuid4().hex}", display_name="模型测试用户", role_code="BIZ")


def test_store_and_reload_user_and_task(factory):
    with factory() as session, session.begin():
        user = make_user()
        session.add(user)
        # flush 把 INSERT 发到数据库并拿到主键，但不会提交事务。
        session.flush()
        task = ContractTask(
            task_no=f"TEST-{uuid4().hex[:20]}",
            contract_name="模型验证合同",
            applicant_id=user.id,
            amount=Decimal("123456.78"),
            currency="CNY",
        )
        session.add(task)
        session.flush()
        task_id = task.id

    # 新会话重新 SELECT，确保验证的是数据库记录，而不是内存中的对象。
    with factory() as session:
        saved = session.get(ContractTask, task_id)
        assert saved is not None
        assert saved.applicant.username == user.username
        assert saved.applicant.is_active is True
        assert saved.amount == Decimal("123456.78")
        assert saved.status == "pending"
        assert saved.source_type == "UPLOAD"
        assert saved.contract_type == "UNKNOWN"
        assert saved.writeback_status == "not_written"
        assert saved.review_version == 1
        assert saved.review_degraded is False
        assert saved.is_deleted is False
        assert saved.created_at is not None
        assert saved.updated_at is not None


def test_duplicate_approval_event_is_rejected(factory):
    event_id = f"test-event-{uuid4().hex}"
    with factory() as session:
        with session.begin():
            user = make_user()
            session.add(user)
            session.flush()
            session.add(
                ContractTask(
                    task_no=f"TEST-{uuid4().hex[:20]}",
                    contract_name="首次审批事件",
                    applicant_id=user.id,
                    source_event_id=event_id,
                )
            )

        with pytest.raises(IntegrityError):
            with session.begin():
                session.add(
                    ContractTask(
                        task_no=f"TEST-{uuid4().hex[:20]}",
                        contract_name="重复审批事件",
                        applicant_id=user.id,
                        source_event_id=event_id,
                    )
                )

        # 唯一键冲突回滚后，会话仍能读取首次成功的记录。
        saved = session.scalar(select(ContractTask).where(ContractTask.source_event_id == event_id))
        assert saved.contract_name == "首次审批事件"


def test_request_session_rolls_back_on_error(factory, monkeypatch):
    monkeypatch.setattr(db, "get_session_factory", lambda: factory)
    dependency = db.get_db()
    session = next(dependency)
    user = make_user()
    username = user.username
    session.add(user)
    session.flush()

    with pytest.raises(RuntimeError, match="模拟业务失败"):
        dependency.throw(RuntimeError("模拟业务失败"))

    with factory() as verification:
        assert verification.scalar(select(AppUser).where(AppUser.username == username)) is None


def test_request_session_does_not_implicitly_commit(factory, monkeypatch):
    monkeypatch.setattr(db, "get_session_factory", lambda: factory)
    dependency = db.get_db()
    session = next(dependency)
    user = make_user()
    username = user.username
    session.add(user)
    session.flush()
    # 正常结束请求也不自动提交未确认的修改。
    with pytest.raises(StopIteration):
        next(dependency)

    with factory() as verification:
        assert verification.scalar(select(AppUser).where(AppUser.username == username)) is None
