"""用真实 MySQL 验证身份、角色和任务权限；记录在测试结束后回滚。"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.models import AppUser, ContractTask

pytestmark = pytest.mark.integration


@pytest.fixture
def records(factory):
    with factory() as session, session.begin():
        users = {}
        for key, role, active in [
            ("legal", "LEGAL", True),
            ("admin", "ADMIN", True),
            ("owner", "BIZ", True),
            ("other", "BIZ", True),
            ("disabled", "LEGAL", False),
        ]:
            user = AppUser(
                username=f"test-{uuid4().hex}",
                display_name="权限测试用户",
                role_code=role,
                is_active=active,
            )
            session.add(user)
            session.flush()
            users[key] = user
        tasks = {}
        for key, deleted in [("visible", False), ("deleted", True)]:
            task = ContractTask(
                task_no=f"TEST-{uuid4().hex[:20]}",
                contract_name="权限测试合同",
                applicant_id=users["owner"].id,
                is_deleted=deleted,
            )
            session.add(task)
            session.flush()
            tasks[key] = task
    return users, tasks


@pytest.fixture
def client(api_app, factory):
    def test_db():
        with factory() as session:
            yield session

    api_app.dependency_overrides[get_db] = test_db
    with TestClient(api_app) as test_client:
        yield test_client


def identity(user, role=None):
    return {"X-User-Id": str(user.id), "X-User-Role": role or user.role_code}


@pytest.mark.parametrize("key", ["legal", "admin", "owner"])
def test_current_user_response_has_only_public_fields(client, records, key):
    users, _ = records
    response = client.get("/api/users/me", headers=identity(users[key]))
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0 and body["message"] == "ok"
    assert body["data"]["id"] == users[key].id
    assert body["data"]["role_code"] == users[key].role_code
    assert set(body["data"]) == {
        "id",
        "username",
        "display_name",
        "role_code",
        "company_name",
        "is_active",
    }


def test_nonexistent_user_is_401(client, records):
    users, _ = records
    missing_id = max(user.id for user in users.values()) + 1
    response = client.get(
        "/api/users/me",
        headers={
            "X-User-Id": str(missing_id),
            "X-User-Role": "LEGAL",
        },
    )
    assert response.status_code == 401
    assert response.json()["code"] == 40100


def test_disabled_user_is_401(client, records):
    users, _ = records
    assert client.get("/api/users/me", headers=identity(users["disabled"])).status_code == 401


def test_spoofed_role_is_403(client, records):
    users, _ = records
    response = client.get("/api/users/me", headers=identity(users["owner"], "ADMIN"))
    assert response.status_code == 403
    assert response.json()["code"] == 40300


@pytest.mark.parametrize("key", ["owner", "legal", "admin"])
def test_owner_legal_and_admin_can_access_task(client, records, key):
    users, tasks = records
    response = client.get(f"/test/tasks/{tasks['visible'].id}", headers=identity(users[key]))
    assert response.status_code == 200
    assert response.json()["id"] == tasks["visible"].id


def test_business_user_cannot_access_others_task(client, records):
    users, tasks = records
    response = client.get(f"/test/tasks/{tasks['visible'].id}", headers=identity(users["other"]))
    assert response.status_code == 403
    assert response.json()["message"] == "无权限操作该资源"


@pytest.mark.parametrize("key", ["owner", "other", "legal", "admin"])
def test_deleted_task_is_404_for_every_role(client, records, key):
    users, tasks = records
    response = client.get(f"/test/tasks/{tasks['deleted'].id}", headers=identity(users[key]))
    assert response.status_code == 404
    assert response.json()["code"] == 40401


def test_missing_task_is_404(client, records):
    users, tasks = records
    missing_id = max(task.id for task in tasks.values()) + 1
    response = client.get(f"/test/tasks/{missing_id}", headers=identity(users["other"]))
    assert response.status_code == 404


@pytest.mark.parametrize("key,expected", [("legal", 200), ("owner", 403), ("admin", 403)])
def test_role_guard_does_not_grant_admin_unrestricted_access(client, records, key, expected):
    users, _ = records
    assert client.get("/test/legal-only", headers=identity(users[key])).status_code == expected
