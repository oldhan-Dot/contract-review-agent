"""验证已完成快照的法务确认与幂等回写；测试数据由外层事务整体回滚。

直接构造测试快照，不代表真实模型效果验证，也不会创建可见的完成演示任务。
"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.workspace import router
from app.core.db import get_db
from app.models import AppUser, ContractTask, ReviewSnapshot

pytestmark = pytest.mark.integration


def test_legal_confirmation_and_idempotent_mock_writeback(api_app, factory):
    api_app.include_router(router)

    def test_db():
        with factory() as db:
            yield db

    api_app.dependency_overrides[get_db] = test_db
    with factory() as db:
        legal = db.scalar(select(AppUser).where(AppUser.username == "legal"))
        task = ContractTask(
            task_no="TEST-" + uuid4().hex[:24],
            contract_name="仅事务内测试合同",
            contract_type="PURCHASE",
            our_party_role="A",
            applicant_id=legal.id,
            status="completed",
            review_version=1,
        )
        db.add(task)
        db.flush()
        db.add(
            ReviewSnapshot(
                task_id=task.id,
                review_version=1,
                revision=0,
                human_review={},
                payload={"complete": True, "rule_risks": {"items": []}, "ai_risks": {"items": []}},
            )
        )
        db.commit()
        task_id, legal_id = task.id, legal.id
    headers = {"X-User-Id": str(legal_id), "X-User-Role": "LEGAL"}
    path = f"/api/tasks/{task_id}"
    body = {"review_version": 1, "expected_revision": 0}
    with TestClient(api_app) as client:
        assert client.post(path + "/writeback", headers=headers, json=body).status_code == 409
        response = client.patch(path + "/review", headers=headers, json={**body, "confirm": True})
        assert response.status_code == 200, response.text
        assert response.json()["data"]["confirmed"]
        body["expected_revision"] = response.json()["data"]["revision"]
        assert client.post(path + "/mock-approval", headers=headers).status_code == 200
        first = client.post(path + "/writeback", headers=headers, json=body)
        second = client.post(path + "/writeback", headers=headers, json=body)
        assert first.status_code == second.status_code == 200, first.text
        assert first.json()["data"]["comment_id"] == second.json()["data"]["comment_id"]
        comments = client.get(path + "/comments", headers=headers).json()["data"]
        assert len(comments) == 1
        assert client.patch(path + "/review", headers=headers, json=body).status_code == 200
        body["expected_revision"] += 1
        assert client.post(path + "/writeback", headers=headers, json=body).status_code == 409
