"""将已授权原件的一页渲染为图片；不重新解析或审查合同。"""

import hashlib
import math
from pathlib import Path

import pymupdf

from app.core.errors import AppError
from app.parsing.image_channel import IMAGE_EXTENSIONS, image_as_pdf


def render_pdf_page(path: str, page_no: int, scale: float, expected_md5: str) -> bytes:
    """验证原件未被替换，按受限尺寸渲染，返回 PNG，不生成公开磁盘地址。"""
    original = Path(path)
    if not original.is_file():
        raise AppError(404, 40401, "原件不存在")
    content = original.read_bytes()
    if hashlib.md5(content, usedforsecurity=False).hexdigest() != expected_md5:
        raise AppError(409, 40901, "原件内容已改变，请重新上传并审查后预览")
    try:
        extension = original.suffix.lower().lstrip(".")
        if extension in IMAGE_EXTENSIONS:
            content = image_as_pdf(content, extension)
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            if pdf.needs_pass:
                raise AppError(409, 40901, "PDF 已加密，无法预览")
            if not 1 <= page_no <= len(pdf):
                raise AppError(404, 40401, "PDF 页码不存在")
            page = pdf[page_no - 1]
            width, height = page.rect.width, page.rect.height
            # 约束超大页面的渲染内存；前端按真实页面比例叠加证据框。
            factor = min(scale, 2400 / max(width, height), math.sqrt(4_000_000 / (width * height)))
            return page.get_pixmap(matrix=pymupdf.Matrix(factor, factor), alpha=False).tobytes(
                "png"
            )
    except AppError:
        raise
    except Exception:
        raise AppError(422, 40001, "PDF 页面无法渲染，请检查原件") from None
