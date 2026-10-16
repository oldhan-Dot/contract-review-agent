"""手工上传 DOCX、PDF 或 PNG/JPEG 合同并创建待处理任务。"""

import hashlib
import os
import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

import pymupdf
from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError
from app.core.security import IdentityHeaders, parse_identity_headers, resolve_user
from app.models import AppUser, ContractFile, ContractTask
from app.parsing.exceptions import ParseBlocked
from app.parsing.image_channel import IMAGE_EXTENSIONS, image_as_pdf
from app.schemas.common import ApiResponse, ErrorResponse
from app.schemas.task import TaskCreated

router = APIRouter(prefix="/api/tasks", tags=["合同任务"])
CHUNK_SIZE = 1024 * 1024
MIN_FILE_SIZE = 1024


def _check_docx(path: Path) -> None:
    """DOCX 必须是包含关键 OOXML 部件的 ZIP，改后缀的普通文件不能入库。"""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise AppError(415, 41501, "文件内容不是有效的 DOCX")
    except (zipfile.BadZipFile, OSError) as exc:
        raise AppError(415, 41501, "文件内容不是有效的 DOCX") from exc


def _stage_upload(file: UploadFile, upload_root: Path, max_bytes: int) -> tuple[Path, int, str]:
    """按块读取并计数；不相信客户端提供的 Content-Length。"""
    upload_root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.md5(
        usedforsecurity=False
    )  # 数据库既有 file_md5 字段用于判重，不用于安全签名。
    staged: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=upload_root, prefix=".upload-", delete=False
        ) as target:
            staged = Path(target.name)
            size = 0
            while chunk := file.file.read(CHUNK_SIZE):
                size += len(chunk)
                if size > max_bytes:
                    raise AppError(413, 41301, "单个文件不超过 50MB")
                target.write(chunk)
                digest.update(chunk)
        extension = Path(file.filename or "").suffix.lower()
        # 合法短 PDF 可能不足 1KB，由 PDF 结构校验决定，不套用 DOCX 最小体积。
        if extension.lstrip(".") in IMAGE_EXTENSIONS:
            try:
                image_as_pdf(staged.read_bytes(), extension.lstrip("."))
            except ParseBlocked as exc:
                status = 413 if exc.reason == "IMAGE_TOO_LARGE" else 415
                raise AppError(status, 41301 if status == 413 else 41501, exc.detail) from None
        elif extension == ".pdf":
            try:
                # 从受大小限制的缓冲区校验，避免 Windows 下库打开损坏文件失败后
                # 仍占用临时文件句柄，导致清理异常覆盖原本的 415 错误。
                with pymupdf.open(stream=staged.read_bytes(), filetype="pdf") as pdf:
                    if not pdf.is_pdf:
                        raise AppError(415, 41501, "文件内容不是有效的 PDF")
            except (pymupdf.FileDataError, pymupdf.EmptyFileError, RuntimeError) as exc:
                raise AppError(415, 41501, "文件内容不是有效的 PDF") from exc
        elif size < MIN_FILE_SIZE:
            raise AppError(400, 40001, "文件至少需要 1KB")
        else:
            _check_docx(staged)
        return staged, size, digest.hexdigest()
    except Exception:
        if staged is not None:
            staged.unlink(missing_ok=True)
        raise


@router.post(
    "/upload",
    status_code=201,
    response_model=ApiResponse[TaskCreated],
    responses={code: {"model": ErrorResponse} for code in (400, 401, 403, 413, 415, 422)},
    summary="上传 DOCX、PDF 或 PNG/JPEG 并创建审查任务",
)
def upload_contract(
    identity: Annotated[IdentityHeaders, Depends(parse_identity_headers)],
    db: Annotated[Session, Depends(get_db)],
    file: Annotated[UploadFile, File()],
    contract_type: Annotated[Literal["PURCHASE", "SALES", "SERVICE", "LABOR"], Form()],
    our_party_role: Annotated[Literal["A", "B"], Form()],
    contract_name: Annotated[str | None, Form(max_length=255)] = None,
    amount: Annotated[Decimal | None, Form(ge=0, max_digits=18, decimal_places=2)] = None,
    currency: Annotated[str | None, Form(max_length=3)] = None,
    applicant_dept: Annotated[str | None, Form(max_length=64)] = None,
):
    # 1. 类型与我方角色由调用方明确填写，不猜测甲方；名称允许后续从原文提取。
    # task 表名称非空，因此先存展示占位，不把占位文字当作原文识别结果。
    name = (contract_name or "").strip() or "待识别合同"
    department = (applicant_dept or "").strip() or None
    original_name = (file.filename or "").replace("\\", "/").split("/")[-1].strip()
    if not original_name or len(original_name) > 255:
        raise AppError(400, 40001, "文件名不能为空且不能超过 255 字符")
    extension = Path(original_name).suffix.lower().lstrip(".")
    if extension not in {"docx", "pdf", *IMAGE_EXTENSIONS}:
        raise AppError(415, 41501, "当前支持 DOCX、PDF、PNG、JPG、JPEG 文件")

    # FastAPI 完成所有表单字段校验后才进入方法，再去数据库确认身份。
    user: AppUser = resolve_user(identity, db)
    # 2. 申请人只取当前数据库身份；管理员/法务不会因角色更高而自动获得上传权。
    if user.role_code != "BIZ":
        raise AppError(403, 40300, "仅业务经办人可以上传合同发起审查")
    settings = get_settings()
    upload_root = settings.storage_path / "uploads"
    staged, file_size, file_md5 = _stage_upload(
        file, upload_root, settings.max_upload_mb * 1024 * 1024
    )
    final_path: Path | None = None
    task_dir: Path | None = None
    try:
        # 锁住申请人行，串行化同一人的上传；并发重复请求才能稳定复用同一任务。
        db.execute(select(AppUser.id).where(AppUser.id == user.id).with_for_update()).scalar_one()
        previous = db.execute(
            select(ContractTask, ContractFile)
            .join(ContractFile, ContractFile.task_id == ContractTask.id)
            .where(
                ContractTask.applicant_id == user.id,
                ContractTask.source_type == "UPLOAD",
                ContractTask.is_deleted.is_(False),
                # 相同文件在不同类型/我方角色/送审部门下有不同审查含义，不能复用。
                ContractTask.contract_type == contract_type,
                ContractTask.our_party_role == our_party_role,
                ContractTask.applicant_dept == department,
                ContractFile.file_md5 == file_md5,
                ContractFile.file_size == file_size,
                ContractFile.file_ext == extension,
            )
            .order_by(ContractTask.id.desc())
            .limit(1)
            # 使用当前读，避免认证查询形成的旧快照看不到并发请求刚提交的任务。
            .with_for_update()
        ).first()
        if previous and Path(previous[1].storage_path).is_file():
            task = previous[0]
            db.commit()  # 释放申请人行锁。
            return ApiResponse[TaskCreated](
                data=TaskCreated(
                    task_id=task.id, task_no=task.task_no, status=task.status, duplicated=True
                )
            )

        task = ContractTask(
            task_no=f"UP-{uuid4().hex[:28]}",
            contract_name=name,
            contract_type=contract_type,
            applicant_id=user.id,
            applicant_dept=department,
            amount=amount,
            currency=currency.upper() if currency else None,
            our_party_role=our_party_role,
            source_type="UPLOAD",
            status="pending",
        )
        db.add(task)
        db.flush()  # 先取得数据库生成的 task_id，文件名才由服务端 ID 构造。
        task_dir = upload_root / str(task.id)
        task_dir.mkdir(parents=True, exist_ok=True)
        final_path = task_dir / f"{task.id}_1.{extension}"
        os.replace(staged, final_path)
        db.add(
            ContractFile(
                task_id=task.id,
                original_name=original_name,
                storage_path=str(final_path),
                file_size=file_size,
                file_md5=file_md5,
                mime_type=(
                    {
                        "pdf": "application/pdf",
                        "png": "image/png",
                        "jpg": "image/jpeg",
                        "jpeg": "image/jpeg",
                    }.get(
                        extension,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                ),
                file_ext=extension,
                file_version=1,
            )
        )
        db.commit()
        return ApiResponse[TaskCreated](
            data=TaskCreated(task_id=task.id, task_no=task.task_no, status="pending")
        )
    except Exception:
        db.rollback()
        if final_path is not None:
            final_path.unlink(missing_ok=True)
        if task_dir is not None and task_dir.exists() and not any(task_dir.iterdir()):
            task_dir.rmdir()
        raise
    finally:
        staged.unlink(missing_ok=True)
