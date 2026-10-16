"""PNG/JPEG 保留原件，规范化为单页 PDF 后复用已验证的 OCR 协议。"""

import hashlib
import struct
from pathlib import Path
from time import perf_counter

import pymupdf

from app.core.config import get_settings
from app.parsing.exceptions import ParseBlocked
from app.parsing.ir import ParsedDocument, SourceFormat
from app.parsing.mineru_client import extract_with_mineru
from app.parsing.ocr_channel import result_to_document

IMAGE_EXTENSIONS = {"png", "jpg", "jpeg"}
MAX_IMAGE_PIXELS = 25_000_000


def _dimensions(content: bytes, extension: str) -> tuple[int, int]:
    """在解码像素前读取尺寸，拒绝伪装格式与超大位图。"""
    if extension == "png":
        if len(content) < 24 or content[:8] != b"\x89PNG\r\n\x1a\n" or content[12:16] != b"IHDR":
            raise ValueError("Invalid PNG")
        return struct.unpack(">II", content[16:24])
    if extension not in {"jpg", "jpeg"} or content[:2] != b"\xff\xd8":
        raise ValueError("Invalid JPEG")
    offset = 2
    while offset + 3 < len(content):
        if content[offset] != 0xFF:
            raise ValueError("Invalid JPEG marker")
        while offset < len(content) and content[offset] == 0xFF:
            offset += 1
        marker = content[offset]
        offset += 1
        length = int.from_bytes(content[offset : offset + 2], "big")
        if length < 2 or offset + length > len(content):
            raise ValueError("Invalid JPEG segment")
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height, width = struct.unpack(">HH", content[offset + 3 : offset + 7])
            return width, height
        if marker in {0xDA, 0xD9}:
            break
        offset += length
    raise ValueError("JPEG dimensions missing")


def image_as_pdf(content: bytes, extension: str) -> bytes:
    """用同一转换方法处理 EXIF 方向和 DPI，确保 OCR 与原图预览使用相同坐标系。"""
    try:
        width, height = _dimensions(content, extension)
        if width <= 0 or height <= 0:
            raise ValueError("Empty image")
        if width * height > MAX_IMAGE_PIXELS:
            raise ParseBlocked("IMAGE_TOO_LARGE", "图片超过 2500 万像素，请缩小后上传")
        with pymupdf.open(stream=content, filetype=extension) as image:
            if len(image) != 1:
                raise ValueError("Expected one image")
            # 小图解码检查可读性；转换保留图像内容，不改变原件文件。
            scale = min(1, 64 / max(image[0].rect.width, image[0].rect.height))
            image[0].get_pixmap(matrix=pymupdf.Matrix(scale, scale))
            return image.convert_to_pdf()
    except ParseBlocked:
        raise
    except Exception:
        raise ParseBlocked("ATTACHMENT_CORRUPT", "图片内容损坏或与扩展名不符") from None


def parse_image(path: str | Path, *, file_id: int) -> ParsedDocument:
    """单张图片作为单页合同；原件仍绑定原 file_id，不创建另一个审查任务。"""
    started = perf_counter()
    path = Path(path)
    if not path.is_file():
        raise ParseBlocked("ATTACHMENT_MISSING", "图片原件不存在")
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    folder = get_settings().storage_path / "renders" / "images"
    folder.mkdir(parents=True, exist_ok=True)
    normalized = folder / f"{digest}-v1.pdf"
    if not normalized.is_file():
        converted = image_as_pdf(content, path.suffix.lower().lstrip("."))
        temporary = normalized.with_suffix(".tmp")
        temporary.write_bytes(converted)
        temporary.replace(normalized)
    document = result_to_document(normalized, extract_with_mineru(normalized), file_id=file_id)
    document.source_format = SourceFormat.IMAGE_OCR
    document.quality.warnings.append(
        "单张图片作为单页合同识别；照片完整性、方向和文字需对照原件核对。"
    )
    document.parse_duration_ms = int((perf_counter() - started) * 1000)
    return document
