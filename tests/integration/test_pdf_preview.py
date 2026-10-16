"""原件页图需要鉴权与版本绑定；测试数据事务回滚，不调用模型。"""

import hashlib
from uuid import uuid4

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.workspace import router
from app.core.db import get_db
from app.models import AppUser, ContractFile, ContractTask, ReviewSnapshot
from app.parsing.pdf_channel import parse_pdf

pytestmark = pytest.mark.integration


def test_preview_pages_permissions_versions_and_original_integrity(api_app, factory, tmp_path):
    path = tmp_path / "original.pdf"
    with pymupdf.open() as pdf:
        for width, height in [(595, 842), (842, 595)]:
            page = pdf.new_page(width=width, height=height)
            page.insert_text(
                (40, 80), "Synthetic contract for preview validation. " * 2, fontsize=9
            )
        pdf.save(path)
    original = path.read_bytes()
    api_app.include_router(router)

    def test_db():
        with factory() as db:
            yield db

    api_app.dependency_overrides[get_db] = test_db
    with factory() as db:
        user = db.scalar(select(AppUser).where(AppUser.username == "biz"))
        other = AppUser(username="preview-" + uuid4().hex, display_name="Other", role_code="BIZ")
        db.add(other)
        task = ContractTask(
            task_no="PREVIEW-" + uuid4().hex[:20],
            contract_name="Synthetic preview",
            applicant_id=user.id,
            contract_type="PURCHASE",
            our_party_role="A",
            status="blocked",
        )
        db.add(task)
        db.flush()
        file = ContractFile(
            task_id=task.id,
            original_name="original.pdf",
            storage_path=str(path),
            file_size=len(original),
            file_md5=hashlib.md5(original, usedforsecurity=False).hexdigest(),
            file_ext="pdf",
            mime_type="application/pdf",
            file_version=1,
        )
        db.add(file)
        db.flush()
        document = parse_pdf(path, file_id=file.id)
        db.add(
            ReviewSnapshot(
                task_id=task.id,
                review_version=1,
                payload={"document": document.model_dump(mode="json")},
                human_review={},
                revision=0,
            )
        )
        db.commit()
        task_id, file_id, user_id, other_id = task.id, file.id, user.id, other.id
    headers = {"X-User-Id": str(user_id), "X-User-Role": "BIZ"}
    params = {"review_version": 1, "file_id": file_id}
    url = f"/api/tasks/{task_id}/pages"
    with TestClient(api_app) as client:
        for page_no in (1, 2):
            response = client.get(f"{url}/{page_no}", headers=headers, params=params)
            assert response.status_code == 200, response.text[:200]
            assert response.headers["content-type"] == "image/png"
            assert response.headers["cache-control"] == "private, no-store"
            image = pymupdf.Pixmap(response.content)
            assert (image.width > image.height) == (page_no == 2)
        assert client.get(f"{url}/1", params=params).status_code == 401
        other_headers = {**headers, "X-User-Id": str(other_id)}
        assert client.get(f"{url}/1", headers=other_headers, params=params).status_code == 403
        assert client.get(f"{url}/3", headers=headers, params=params).status_code == 404
        assert (
            client.get(
                f"{url}/1", headers=headers, params={**params, "review_version": 2}
            ).status_code
            == 409
        )
        assert (
            client.get(
                f"{url}/1", headers=headers, params={**params, "file_id": file_id + 1}
            ).status_code
            == 409
        )
        path.write_bytes(original + b"changed")
        assert client.get(f"{url}/1", headers=headers, params=params).status_code == 409
