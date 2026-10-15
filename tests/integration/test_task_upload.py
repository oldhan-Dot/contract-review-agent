"""真实 MySQL 与临时目录共同验证上传结果，测试后不保留任务或文件。"""

from importlib import import_module
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pymupdf
import pytest
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import tasks
from app.core.db import get_db
from app.models import AppUser, ContractFile, ContractTask

pytestmark = pytest.mark.integration


@pytest.fixture
def upload_client(api_app, factory, tmp_path, monkeypatch):
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(storage_path=tmp_path, max_upload_mb=50),
    )

    def test_db():
        with factory() as session:
            yield session

    api_app.dependency_overrides[get_db] = test_db
    with factory() as session:
        user = AppUser(
            username=f"upload-{uuid4().hex}",
            display_name="上传测试用户",
            role_code="BIZ",
            is_active=True,
        )
        session.add(user)
        session.commit()
        user_id = user.id
    with TestClient(api_app) as client:
        yield client, factory, tmp_path, user_id


def docx_bytes():
    document = Document()
    document.add_paragraph("采购合同测试正文")
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def test_pdf_upload_reaches_real_parse_and_structure_nodes(upload_client, monkeypatch):
    from app.graph.nodes.structure_document import structure_document

    client, factory, root, user_id = upload_client
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        for i, text in enumerate(
            [
                "软件采购合同",
                "甲方：合成采购有限公司",
                "乙方：合成开发有限公司",
                "第一条 合同金额",
                "合同总金额：人民币100000.00元，甲方在验收合格后支付价款。",
                "第二条 保密义务",
                "双方对商业秘密保密至合同终止后三年。",
            ]
        ):
            page.insert_text((40, 60 + 30 * i), text, fontname="china-s", fontsize=10)
        content = pdf.tobytes()
    response = send(client, user_id, content, filename="合同.pdf")
    assert response.status_code == 201, response.text
    task_id = response.json()["data"]["task_id"]
    with factory() as db:
        file = db.scalar(select(ContractFile).where(ContractFile.task_id == task_id))
        assert file.file_ext == "pdf" and file.mime_type == "application/pdf"
        state = dict(task_id=task_id, file_id=file.id, review_version=1)
    assert (root / "uploads" / str(task_id) / f"{task_id}_1.pdf").read_bytes() == content
    module = import_module("app.graph.nodes.parse_document")
    monkeypatch.setattr(module, "get_session_factory", lambda: factory)
    state.update(module.parse_document(state))
    result = structure_document(state)
    assert state["document"].source_format == "PDF_TEXT"
    assert result["metadata"].get("amount").field_value == "100000.00"
    assert result["metadata"].get("amount").anchor.page_no == 1


def send(client, user_id, content, filename="合同.docx", **fields):
    return client.post(
        "/api/tasks/upload",
        headers={"X-User-Id": str(user_id), "X-User-Role": "BIZ"},
        data={
            "contract_name": "测试采购合同",
            "contract_type": "PURCHASE",
            "our_party_role": "A",
            **fields,
        },
        files={"file": (filename, content, "application/octet-stream")},
    )


def test_valid_upload_and_duplicate_reuse(upload_client):
    client, factory, root, user_id = upload_client
    content = docx_bytes()
    first = send(client, user_id, content, amount="12.50")
    assert first.status_code == 201, first.text
    created = first.json()["data"]
    assert created["status"] == "pending" and created["duplicated"] is False

    with factory() as session:
        task = session.get(ContractTask, created["task_id"])
        file = session.scalar(select(ContractFile).where(ContractFile.task_id == task.id))
        assert task.contract_name == "测试采购合同"
        assert task.applicant_id == user_id
        assert str(task.amount) == "12.50"
        assert file.file_size == len(content)
        assert file.original_name == "合同.docx"
        assert (root / "uploads" / str(task.id) / f"{task.id}_1.docx").read_bytes() == content

    second = send(client, user_id, content)
    assert second.status_code == 201
    assert second.json()["data"]["task_id"] == created["task_id"]
    assert second.json()["data"]["duplicated"] is True
    with factory() as session:
        assert (
            len(
                session.scalars(
                    select(ContractTask).where(ContractTask.applicant_id == user_id)
                ).all()
            )
            == 1
        )


@pytest.mark.parametrize(
    ("filename", "content", "fields", "http_status", "code"),
    [
        ("contract.exe", b"x" * 2048, {}, 415, 41501),
        ("contract.docx", b"not a docx" * 200, {}, 415, 41501),
        ("contract.pdf", b"not a pdf" * 200, {}, 415, 41501),
        ("contract.docx", b"", {}, 400, 40001),
        ("contract.docx", b"x" * (50 * 1024 * 1024 + 1), {}, 413, 41301),
        ("contract.docx", b"x" * 2048, {"contract_name": "名" * 256}, 422, 40001),
    ],
    ids=["unsupported-extension", "fake-docx", "fake-pdf", "empty", "over-limit", "overlong-name"],
)
def test_invalid_upload_creates_nothing(
    upload_client, filename, content, fields, http_status, code
):
    client, factory, root, user_id = upload_client
    response = send(client, user_id, content, filename, **fields)
    assert response.status_code == http_status
    assert response.json()["code"] == code
    with factory() as session:
        assert (
            session.scalar(select(ContractTask.id).where(ContractTask.applicant_id == user_id))
            is None
        )
    if (root / "uploads").exists():
        assert list((root / "uploads").rglob("*.docx")) == []


def test_commit_failure_rolls_back_and_removes_file(upload_client, monkeypatch):
    client, factory, root, user_id = upload_client

    def fail_commit(session):
        raise RuntimeError("模拟数据库提交失败")

    # 用户已在 fixture 中准备完毕；仅让本次上传的提交失败。
    monkeypatch.setattr(factory.class_, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="模拟数据库提交失败"):
        send(client, user_id, docx_bytes())
    with factory() as session:
        assert (
            session.scalar(select(ContractTask.id).where(ContractTask.applicant_id == user_id))
            is None
        )
    assert list((root / "uploads").iterdir()) == []


def test_different_applicants_do_not_share_task(upload_client):
    client, factory, _, user_id = upload_client
    content = docx_bytes()
    first = send(client, user_id, content)
    assert first.status_code == 201
    with factory() as session:
        other = AppUser(
            username=f"other-{uuid4().hex}",
            display_name="其他申请人",
            role_code="BIZ",
            is_active=True,
        )
        session.add(other)
        session.commit()
        other_id = other.id
    second = send(client, other_id, content)
    assert second.status_code == 201
    assert second.json()["data"]["duplicated"] is False
    assert second.json()["data"]["task_id"] != first.json()["data"]["task_id"]


@pytest.mark.parametrize("name", [None, "", "   "])
def test_optional_title_uses_display_placeholder(upload_client, name):
    client, factory, _, user_id = upload_client
    response = send(client, user_id, docx_bytes(), contract_name=name)
    assert response.status_code == 201
    with factory() as session:
        task = session.get(ContractTask, response.json()["data"]["task_id"])
        assert task.contract_name == "待识别合同"
        assert task.contract_type == "PURCHASE"
        assert task.our_party_role == "A"


@pytest.mark.parametrize(
    "changes",
    [
        {"our_party_role": "B"},
        {"contract_type": "SALES"},
        {"applicant_dept": "销售部"},
    ],
)
def test_same_file_with_different_review_context_creates_new_task(upload_client, changes):
    client, factory, _, user_id = upload_client
    content = docx_bytes()
    first = send(client, user_id, content)
    second = send(client, user_id, content, **changes)
    assert first.status_code == second.status_code == 201
    assert second.json()["data"]["duplicated"] is False
    assert first.json()["data"]["task_id"] != second.json()["data"]["task_id"]
    with factory() as session:
        saved = session.get(ContractTask, second.json()["data"]["task_id"])
        for field, value in changes.items():
            assert getattr(saved, field) == value


def test_normalized_department_reuses_same_context(upload_client):
    client, _, _, user_id = upload_client
    content = docx_bytes()
    first = send(client, user_id, content, applicant_dept="采购部")
    second = send(client, user_id, content, applicant_dept=" 采购部 ")
    assert first.status_code == second.status_code == 201
    assert second.json()["data"]["task_id"] == first.json()["data"]["task_id"]
    assert second.json()["data"]["duplicated"] is True


@pytest.mark.parametrize("role", ["LEGAL", "ADMIN"])
def test_non_business_role_cannot_upload_or_write_files(upload_client, role):
    client, factory, root, user_id = upload_client
    with factory() as session:
        user = session.get(AppUser, user_id)
        user.role_code = role
        session.commit()
    response = client.post(
        "/api/tasks/upload",
        headers={"X-User-Id": str(user_id), "X-User-Role": role},
        data={"contract_type": "PURCHASE", "our_party_role": "A"},
        files={"file": ("contract.docx", docx_bytes())},
    )
    assert response.status_code == 403
    with factory() as session:
        assert (
            session.scalar(select(ContractTask.id).where(ContractTask.applicant_id == user_id))
            is None
        )
    assert not (root / "uploads").exists()


def test_applicant_cannot_be_overridden_by_form(upload_client):
    client, factory, _, user_id = upload_client
    response = send(client, user_id, docx_bytes(), applicant_id=str(user_id + 100000))
    assert response.status_code == 201
    with factory() as session:
        task = session.get(ContractTask, response.json()["data"]["task_id"])
        assert task.applicant_id == user_id
