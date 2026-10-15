"""格式不合法时必须在查库前拒绝；此组测试无需 MySQL。"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import tasks
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
        {"contract_type": "UNKNOWN"},
        {"our_party_role": "甲方"},
        {"our_party_role": "a"},
        {"contract_name": "名" * 256},
    ],
)
def test_invalid_upload_form_is_rejected_before_user_lookup(client, fields):
    response = client.post(
        "/api/tasks/upload",
        headers={"X-User-Id": "1", "X-User-Role": "BIZ"},
        data={"contract_type": "PURCHASE", "our_party_role": "A", **fields},
        files={"file": ("contract.docx", b"placeholder")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == 40001
    assert any(
        error["field"] == f"body.{next(iter(fields))}" for error in response.json()["errors"]
    )


@pytest.mark.parametrize("missing", ["contract_type", "our_party_role"])
def test_required_review_context_is_not_defaulted_or_queried(client, missing):
    fields = {"contract_type": "PURCHASE", "our_party_role": "A"}
    fields.pop(missing)
    response = client.post(
        "/api/tasks/upload",
        headers={"X-User-Id": "1", "X-User-Role": "BIZ"},
        data=fields,
        files={"file": ("contract.docx", b"placeholder")},
    )
    assert response.status_code == 422
    assert any(error["field"] == f"body.{missing}" for error in response.json()["errors"])


@pytest.mark.parametrize("role", ["LEGAL", "ADMIN"])
def test_verified_non_business_identity_denied_before_file_write(api_app, monkeypatch, role):
    user = SimpleNamespace(id=1, role_code=role, is_active=True)
    api_app.dependency_overrides[get_db] = lambda: SimpleNamespace(get=lambda *args: user)

    def forbidden(*args, **kwargs):
        raise AssertionError("非业务角色不得暂存上传文件")

    monkeypatch.setattr(tasks, "_stage_upload", forbidden)
    with TestClient(api_app) as test_client:
        response = test_client.post(
            "/api/tasks/upload",
            headers={"X-User-Id": "1", "X-User-Role": role},
            data={"contract_type": "PURCHASE", "our_party_role": "B"},
            files={"file": ("contract.docx", b"placeholder")},
        )
    assert response.status_code == 403
    assert response.json()["code"] == 40300


def test_openapi_upload_requires_context_but_not_title(api_app):
    schema = api_app.openapi()
    body = schema["paths"]["/api/tasks/upload"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["schema"]
    form = schema["components"]["schemas"][body["$ref"].rsplit("/", 1)[-1]]
    assert set(form["required"]) == {"file", "contract_type", "our_party_role"}
    assert "default" not in form["properties"]["our_party_role"]
