"""首版任务大盘、复核工作台与管理接口；所有业务操作均经过身份和任务权限。"""

import hashlib
from datetime import datetime, timezone
from io import BytesIO
from typing import Annotated, Literal

from docx import Document
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError
from app.core.security import IdentityHeaders, get_current_user, require_task_access
from app.models import AppUser, ContractFile, ContractTask, ReviewRuleSetting
from app.review.engine import CATALOG, rule_catalog
from app.services.pdf_preview import render_pdf_page
from app.services.reports import markdown_report, pdf_report
from app.services.result_store import result_risks, snapshot_for
from app.services.runner import runner

router = APIRouter(prefix="/api", tags=["合同工作台"])
Db = Annotated[Session, Depends(get_db)]
User = Annotated[AppUser, Depends(get_current_user)]
Task = Annotated[ContractTask, Depends(require_task_access)]


def ok(data):
    return {"code": 0, "message": "ok", "data": data}


def locked_task(db, task_id):
    return db.scalar(
        select(ContractTask)
        .where(ContractTask.id == task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def task_data(task, applicant=None):
    return {
        "id": task.id,
        "task_no": task.task_no,
        "contract_name": task.contract_name,
        "contract_type": task.contract_type,
        "our_party_role": task.our_party_role,
        "applicant": applicant or str(task.applicant_id),
        "applicant_id": task.applicant_id,
        "status": task.status,
        "review_version": task.review_version,
        "amount": str(task.amount) if task.amount is not None else None,
        "currency": task.currency,
        "overall_risk_level": task.overall_risk_level,
        "review_summary": task.review_summary,
        "block_reason": task.block_reason,
        "block_detail": task.block_detail,
        "writeback_status": task.writeback_status,
        "approval_no": task.approval_no,
        "created_at": task.created_at.isoformat(),
    }


@router.get("/demo/users")
def demo_users(db: Db):
    if get_settings().app_env != "dev":
        raise AppError(404, 40401, "演示身份入口未开放")
    users = db.scalars(
        select(AppUser).where(
            AppUser.username.in_(["biz", "legal", "admin"]), AppUser.is_active.is_(True)
        )
    )
    return ok(
        [{"id": user.id, "name": user.display_name, "role": user.role_code} for user in users]
    )


@router.get("/workspace")
def workspace_capabilities(user: User):
    settings = get_settings()
    return ok(
        {
            "runner": runner.running,
            "llm_configured": settings.llm_enabled
            and bool(settings.llm_api_key.get_secret_value()),
            # 仅暴露非敏感运行参数，便于核对终端环境变量是否覆盖 .env。
            "llm_timeout_seconds": settings.llm_timeout_seconds,
            "llm_max_retries": settings.llm_max_retries,
            "formats": ["DOCX", "PDF_TEXT", "PDF_OCR", "IMAGE_OCR"],
            "ocr_configured": settings.mineru_enabled
            and bool(settings.mineru_api_token.get_secret_value()),
            "pending": ["多图片合并合同", "Word 转换预览映射", "企业信用实际查询"],
            "identity_mode": "预置身份演示，不是正式登录认证",
        }
    )


@router.get("/tasks")
def task_list(
    user: User,
    db: Db,
    status: str | None = None,
    risk_level: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
):
    filters = [ContractTask.is_deleted.is_(False)]
    if user.role_code == "BIZ":
        filters.append(ContractTask.applicant_id == user.id)
    stats = dict(
        db.execute(
            select(ContractTask.status, func.count()).where(*filters).group_by(ContractTask.status)
        ).all()
    )
    if status:
        filters.append(ContractTask.status == status)
    if risk_level:
        filters.append(ContractTask.overall_risk_level == risk_level)
    total = db.scalar(select(func.count()).select_from(ContractTask).where(*filters))
    rows = db.execute(
        select(ContractTask, AppUser.display_name)
        .join(AppUser, AppUser.id == ContractTask.applicant_id)
        .where(*filters)
        .order_by(ContractTask.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return ok(
        {
            "items": [task_data(task, name) for task, name in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "stats": stats,
        }
    )


@router.get("/tasks/{task_id}")
def task_detail(task: Task, db: Db):
    snapshot = snapshot_for(db, task)
    payload = snapshot.payload if snapshot else {}
    return ok(
        {
            "task": task_data(task, task.applicant.display_name),
            "document": payload.get("document"),
            "metadata": payload.get("metadata"),
            "clauses": payload.get("clauses", []),
            "risks": result_risks(payload),
            "complete": payload.get("complete", False),
            "stage": payload.get("stage"),
            "coverage_notes": payload.get("coverage_notes", []),
            "human_review": snapshot.human_review if snapshot else {},
            "revision": snapshot.revision if snapshot else 0,
            "confirmed": snapshot is not None and snapshot.confirmed_revision == snapshot.revision,
        }
    )


@router.get("/tasks/{task_id}/original")
def original_file(task: Task, db: Db):
    from pathlib import Path

    file = db.scalar(
        select(ContractFile)
        .where(ContractFile.task_id == task.id)
        .order_by(ContractFile.file_version.desc())
        .limit(1)
    )
    if not file or not Path(file.storage_path).is_file():
        raise AppError(404, 40401, "原件不存在")
    return FileResponse(file.storage_path, filename=file.original_name, media_type=file.mime_type)


@router.get("/tasks/{task_id}/pages/{page_no}")
def pdf_page(
    task: Task,
    db: Db,
    page_no: int,
    review_version: int = Query(gt=0),
    file_id: int = Query(gt=0),
    scale: float = Query(default=1.5, ge=0.5, le=2),
):
    """先检查权限、快照和文件版本，再提供原 PDF 页面，防止旧证据叠到新原件。"""
    snapshot = snapshot_for(db, task)
    document = snapshot.payload.get("document", {}) if snapshot else {}
    if task.review_version != review_version or document.get("file_id") != file_id:
        raise AppError(409, 40901, "预览版本已改变，请刷新审查结果")
    if document.get("source_format") not in {"PDF_TEXT", "PDF_OCR", "IMAGE_OCR"}:
        raise AppError(409, 40901, "当前原件没有可用的 PDF 预览")
    file = db.scalar(
        select(ContractFile).where(
            ContractFile.id == file_id,
            ContractFile.task_id == task.id,
            ContractFile.file_ext.in_(["pdf", "png", "jpg", "jpeg"]),
        )
    )
    latest = db.scalar(
        select(ContractFile.id)
        .where(ContractFile.task_id == task.id)
        .order_by(
            ContractFile.file_version.desc(),
            ContractFile.id.desc(),
        )
        .limit(1)
    )
    if file is None or file.id != latest:
        raise AppError(409, 40901, "原件版本已改变，请重新审查")
    image = render_pdf_page(file.storage_path, page_no, scale, file.file_md5)
    return Response(image, media_type="image/png", headers={"Cache-Control": "private, no-store"})


class RetryRequest(BaseModel):
    review_version: int = Field(gt=0)


@router.post("/tasks/{task_id}/retry")
def retry_task(task: Task, body: RetryRequest, user: User, db: Db):
    if user.role_code != "ADMIN":
        raise AppError(403, 40300, "仅管理员可触发重新审查")
    task = locked_task(db, task.id)
    if task.review_version != body.review_version or task.status not in {"blocked", "completed"}:
        raise AppError(409, 40901, "任务版本已改变或正在处理中")
    task.review_version += 1
    task.status, task.writeback_status = "pending", "not_written"
    task.block_reason = task.block_detail = task.overall_risk_level = task.review_summary = None
    task.review_finished_at = task.review_conclusion = None
    task.review_degraded, task.review_degraded_reason = False, None
    db.commit()
    return ok({"review_version": task.review_version, "status": task.status})


class ReviewEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_version: int = Field(gt=0)
    expected_revision: int = Field(ge=0)
    suggestions: dict[str, str] = Field(default_factory=dict, max_length=100)
    comment: str = Field(default="", max_length=10000)
    confirm: bool = False


@router.patch("/tasks/{task_id}/review")
def save_human_review(task: Task, body: ReviewEdit, user: User, db: Db):
    if user.role_code != "LEGAL":
        raise AppError(403, 40300, "仅法务可保存并确认审查意见")
    task = locked_task(db, task.id)
    snapshot = snapshot_for(db, task, lock=True)
    if (
        task.review_version != body.review_version
        or snapshot is None
        or snapshot.revision != body.expected_revision
        or task.status not in {"completed", "blocked"}
    ):
        raise AppError(409, 40901, "结果尚未准备好或已改变，请刷新后再编辑")
    allowed = {risk["id"] for risk in result_risks(snapshot.payload)}
    if set(body.suggestions) - allowed or any(
        len(value) > 8000 for value in body.suggestions.values()
    ):
        raise AppError(422, 40001, "建议包含未知风险或超过长度限制")
    if body.confirm and (task.status != "completed" or not snapshot.payload.get("complete")):
        raise AppError(409, 40902, "关键审查尚未完成，可保存草稿，不能确认完整审查")
    final = {**snapshot.human_review.get("suggestions", {}), **body.suggestions}
    if body.confirm and any(
        not final.get(risk["id"], (risk.get("suggestion") or {}).get("text", "")).strip()
        for risk in result_risks(snapshot.payload)
    ):
        raise AppError(422, 40001, "请补充每项风险的最终修改建议")
    snapshot.revision += 1
    snapshot.human_review = {
        "suggestions": final,
        "comment": body.comment,
        "operator_id": user.id,
        "operator": user.display_name,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    snapshot.confirmed_revision = snapshot.revision if body.confirm else None
    task.writeback_status = "not_written"
    db.commit()
    return ok({"revision": snapshot.revision, "confirmed": body.confirm})


def associate_mock(task, db):
    if not task.approval_no:
        number = "MOCK-" + task.task_no
        db.execute(
            text(
                "INSERT INTO mock_approval "
                "(approval_no,contract_name,applicant,contract_type,attachment_url,pushed) "
                "VALUES (:no,:name,:who,:kind,:url,1)"
            ),
            dict(
                no=number,
                name=task.contract_name,
                who=task.applicant.display_name,
                kind=task.contract_type,
                url=f"/api/tasks/{task.id}/original",
            ),
        )
        task.approval_no = number
    return task.approval_no


@router.post("/tasks/{task_id}/mock-approval")
def mock_approval(task: Task, user: User, db: Db):
    if get_settings().app_env != "dev" or user.role_code not in {"LEGAL", "ADMIN"}:
        raise AppError(403, 40300, "仅开发演示中的法务/管理员可关联模拟审批单")
    task = locked_task(db, task.id)
    number = associate_mock(task, db)
    db.commit()
    return ok({"approval_no": number})


@router.post("/tasks/{task_id}/writeback")
def writeback(task: Task, body: ReviewEdit, user: User, db: Db):
    if user.role_code != "LEGAL":
        raise AppError(403, 40300, "仅法务可写回意见")
    task = locked_task(db, task.id)
    snapshot = snapshot_for(db, task, lock=True)
    if (
        snapshot is None
        or task.status != "completed"
        or task.review_version != body.review_version
        or snapshot.revision != body.expected_revision
        or snapshot.confirmed_revision != snapshot.revision
        or not task.approval_no
    ):
        raise AppError(409, 40902, "请先完成审查、法务确认并关联审批单")
    content = markdown_report(task, snapshot)
    # 不把回写状态写入报告，重复请求得到相同内容与幂等键。
    key = hashlib.sha256(f"{task.id}:{task.review_version}:{content}".encode()).hexdigest()
    existing = db.execute(
        text(
            "SELECT comment_id FROM mock_idempotency WHERE approval_no=:no AND idempotency_key=:key"
        ),
        dict(no=task.approval_no, key=key),
    ).scalar_one_or_none()
    if existing is None:
        db.execute(
            text("SELECT id FROM mock_approval WHERE approval_no=:no FOR UPDATE"),
            {"no": task.approval_no},
        )
        serial = db.execute(
            text("SELECT COALESCE(MAX(comment_no),0)+1 FROM mock_comment WHERE approval_no=:no"),
            {"no": task.approval_no},
        ).scalar_one()
        insert = db.execute(
            text(
                "INSERT INTO mock_comment (approval_no,author,content,comment_no) "
                "VALUES (:no,:author,:content,:serial)"
            ),
            dict(no=task.approval_no, author=user.display_name, content=content, serial=serial),
        )
        existing = insert.lastrowid
        db.execute(
            text(
                "INSERT INTO mock_idempotency (approval_no,idempotency_key,comment_id) "
                "VALUES (:no,:key,:id)"
            ),
            dict(no=task.approval_no, key=key, id=existing),
        )
    task.writeback_status = "success"
    db.commit()
    return ok({"writeback_status": "success", "comment_id": existing, "mode": "mock"})


@router.get("/tasks/{task_id}/comments")
def mock_comments(task: Task, db: Db):
    rows = (
        db.execute(
            text(
                "SELECT id,author,content,created_at FROM mock_comment "
                "WHERE approval_no=:no ORDER BY id DESC"
            ),
            {"no": task.approval_no or ""},
        )
        .mappings()
        .all()
    )
    return ok([dict(row) for row in rows])


@router.get("/tasks/{task_id}/report")
def report(task: Task, db: Db, format: Literal["md", "pdf"] = "md", download: bool = False):
    snapshot = snapshot_for(db, task)
    if snapshot is None:
        raise AppError(409, 40901, "尚未产生可导出的结果")
    markdown = markdown_report(task, snapshot)
    if not download:
        return ok(
            {
                "markdown": markdown,
                "review_version": task.review_version,
                "partial": not snapshot.payload.get("complete", False),
            }
        )
    content = pdf_report(markdown) if format == "pdf" else markdown.encode("utf-8")
    return Response(
        content,
        media_type="application/pdf" if format == "pdf" else "text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="review-{task.id}-v{task.review_version}.{format}"'
            )
        },
    )


@router.get("/rules")
def rules(user: User, db: Db):
    if user.role_code == "BIZ":
        raise AppError(403, 40300, "规则管理仅对法务和管理员开放")
    return ok(rule_catalog(db))


class RuleEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    enabled: bool
    level: Literal["HIGH", "MEDIUM", "LOW"]
    suggestion: str = Field(min_length=1, max_length=8000)
    basis: str = Field(min_length=1, max_length=2000)


@router.patch("/rules/{code}")
def edit_rule(code: str, body: RuleEdit, user: User, db: Db):
    if user.role_code != "ADMIN":
        raise AppError(403, 40300, "仅管理员可维护规则与模板")
    if code not in {rule["code"] for rule in CATALOG}:
        raise AppError(404, 40401, "规则不存在")
    row = db.scalar(
        select(ReviewRuleSetting).where(ReviewRuleSetting.rule_code == code).with_for_update()
    )
    if (row.version if row else 0) != body.expected_version:
        raise AppError(409, 40901, "规则已修改，请刷新后重试")
    payload = body.model_dump(exclude={"expected_version"})
    if not row:
        row = ReviewRuleSetting(rule_code=code, version=0, payload={})
        db.add(row)
    row.payload, row.version = payload, row.version + 1
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, 40901, "规则已被其他请求创建，请刷新") from None
    return ok({"version": row.version})


@router.post("/demo/task")
def demo_task(user: User, db: Db):
    if get_settings().app_env != "dev" or user.role_code != "BIZ":
        raise AppError(403, 40300, "请以业务演示身份创建样例")
    from app.api.tasks import upload_contract

    word = Document()
    for paragraph in [
        "定制软件采购合同",
        "合同编号：DEMO-SOFTWARE-001",
        "甲方：合成采购有限公司",
        "统一社会信用代码：91350211M000100Y46",
        "乙方：合成开发有限公司",
        "统一社会信用代码：913100001234567896",
        "第一条 项目约定",
        "本合同为甲方独资委托的定制开发，甲方要求取得定制成果及源代码知识产权。",
        "第二条 金额与付款",
        "合同总金额：人民币100000.00元。甲方在交付当天一次性支付全部价款，无需经过验收。",
        "第三条 知识产权",
        "定制成果及源代码知识产权归乙方所有，甲方仅取得一年使用权。",
        "第四条 违约责任",
        "甲方承担无上限的赔偿责任，包括乙方间接损失；乙方不承担任何违约责任。",
        "第五条 保密义务",
        "双方对商业秘密保密至合同终止后三年。",
        "第六条 不可抗力",
        "受影响方应在事件发生后五个工作日内书面通知对方。",
        "第七条 履行期限",
        "合同期限：2026年12月1日至2027年11月30日。本合同自双方盖章后生效。",
        "签署日期：2026年10月15日",
        "本文件为合成演示数据，不是真实业务合同。",
    ]:
        word.add_paragraph(paragraph)
    stream = BytesIO()
    word.save(stream)
    stream.seek(0)
    return upload_contract(
        identity=IdentityHeaders(user.id, user.role_code),
        db=db,
        file=UploadFile(stream, filename="合成软件采购合同.docx"),
        contract_type="PURCHASE",
        our_party_role="A",
        contract_name="定制软件采购合同 · 合成演示",
        applicant_dept="演示业务部",
    )
