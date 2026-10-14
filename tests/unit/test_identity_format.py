"""格式不合法时必须在查库前拒绝；此组测试无需 MySQL。"""

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db


class NoDatabaseQueries:
    def get(self, *args, **kwargs):
        raise AssertionError("请求格式未通过，不应该查询用户")

    def scalar(self, *args, **kwargs):
        raise AssertionError("请求格式未通过，不应该查询任务")


@pytest.fixture
def client(api_app):
    api_app.dependency_overrides[get_db] = lambda: NoDatabaseQueries()
    with TestClient(api_app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-User-Id": "1"},
        {"X-User-Role": "LEGAL"},
    ],
)
def test_missing_identity_is_401_without_query(client, headers):
    response = client.get("/api/users/me", headers=headers)
    assert response.status_code == 401
    assert response.json()["code"] == 40100
    assert response.json()["data"] is None


@pytest.mark.parametrize("user_id", ["not-an-id", "0", "-1", "1.5", str(2**64)])
def test_invalid_user_id_is_422_without_query(client, user_id):
    response = client.get(
        "/api/users/me",
        headers={
            "X-User-Id": user_id,
            "X-User-Role": "LEGAL",
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 40001
    assert body["errors"][0]["field"] == "header.X-User-Id"
    assert all("input" not in error for error in body["errors"])


@pytest.mark.parametrize("role", ["ROOT", "legal", ""])
def test_invalid_role_is_422_without_query(client, role):
    response = client.get("/api/users/me", headers={"X-User-Id": "1", "X-User-Role": role})
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "header.X-User-Role"


@pytest.mark.parametrize("task_id", ["not-an-id", "0", "-1", str(2**64)])
def test_invalid_task_id_is_422_before_user_lookup(client, task_id):
    response = client.get(
        f"/test/tasks/{task_id}",
        headers={
            "X-User-Id": "1",
            "X-User-Role": "LEGAL",
        },
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "path.task_id"


@pytest.mark.parametrize(
    "fields",
    [
        {"amount": "not-a-number"},
        {"amount": "-1"},
        {"amount": "1.234"},
        {"contract_type": "OTHER"},
    ],
)
def test_invalid_upload_form_is_rejected_before_user_lookup(client, fields):
    response = client.post(
        "/api/tasks/upload",
        headers={"X-User-Id": "1", "X-User-Role": "BIZ"},
        data={"contract_name": "合同", **fields},
        files={"file": ("contract.docx", b"placeholder")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == 40001
