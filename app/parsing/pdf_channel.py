"""文本型 PDF 到统一 IR；只读取文本层，不调用 OCR 或模型。"""

from pathlib import Path
from time import perf_counter

import pymupdf

from app.parsing.exceptions import ParseBlocked
from app.parsing.ir import BBox, LineBox, PageNode, ParagraphNode, ParsedDocument, QualityReport


def _box(page, coordinates):
    # 提取坐标基于未旋转页面，统一转到实际显示方向并裁剪到可见区域。
    rect = (pymupdf.Rect(coordinates) * page.rotation_matrix) & page.rect
    if rect.is_empty:
        raise ParseBlocked("PARSE_ERROR", "PDF 文字坐标超出可见页面，需人工检查")
    return BBox(
        x0=rect.x0,
        y0=rect.y0,
        x1=rect.x1,
        y1=rect.y1,
        page_width=page.rect.width,
        page_height=page.rect.height,
    )


def parse_pdf(path: str | Path, *, file_id: int, doc_id: str | None = None) -> ParsedDocument:
    """按页、文本块、行读取，保留换行和位置；无可靠文本的扫描页明确受阻。"""
    if isinstance(file_id, bool) or not isinstance(file_id, int) or not 0 < file_id < 2**64:
        raise ValueError("file_id 必须是数据库范围内的正整数")
    if doc_id is not None and not doc_id.strip():
        raise ValueError("doc_id 不能为空")
    path = Path(path)
    if not path.is_file():
        raise ParseBlocked("ATTACHMENT_MISSING", "PDF 原件不存在")
    started = perf_counter()
    identifier = doc_id or f"file-{file_id}"
    paragraphs, pages, warnings = [], [], []
    cursor = 0
    try:
        with pymupdf.open(path) as source:
            if not source.is_pdf:
                raise ParseBlocked("ATTACHMENT_CORRUPT", "文件内容不是 PDF")
            if source.needs_pass:
                raise ParseBlocked("ENCRYPTED_FILE", "PDF 已加密，请提供可读取的原件")
            if source.is_repaired:
                warnings.append("PDF 文件结构经过自动修复，请核对文字与原件")
            for page in source:
                indexes = []
                # 不在字典中载入图片字节，避免扫描件占用大量额外内存。
                blocks = page.get_text(
                    "dict",
                    sort=True,
                    flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES,
                )["blocks"]
                for block in blocks:
                    if block.get("type") != 0:
                        continue
                    lines, boxes, offset = [], [], 0
                    for line in block["lines"]:
                        text = "".join(span["text"] for span in line["spans"])
                        if not text.strip():
                            continue
                        boxes.append(
                            LineBox(
                                char_start=offset,
                                char_end=offset + len(text),
                                bbox=_box(page, line["bbox"]),
                            )
                        )
                        lines.append(text)
                        offset += len(text) + 1
                    if not lines:
                        continue
                    text = "\n".join(lines)
                    index = len(paragraphs)
                    paragraphs.append(
                        ParagraphNode(
                            pid=f"{identifier}:p{index}",
                            index=index,
                            page_no=page.number + 1,
                            text=text,
                            char_start=cursor,
                            char_end=cursor + len(text),
                            bbox=_box(page, block["bbox"]),
                            line_boxes=boxes,
                            in_table=None,
                        )
                    )
                    indexes.append(index)
                    cursor += len(text) + 1

                page_text = "\n".join(paragraphs[i].text for i in indexes)
                # 大幅图片却只有少量页眉文字时，也不能把页眉当作完整合同正文。
                images = page.get_image_info()
                large_image = any(
                    (pymupdf.Rect(image["bbox"]) & page.cropbox).get_area()
                    > page.cropbox.get_area() * 0.5
                    for image in images
                )
                if images and (
                    not page_text.strip() or (large_image and len(page_text.strip()) < 50)
                ):
                    raise ParseBlocked(
                        "OCR_REQUIRED", f"PDF 第 {page.number + 1} 页缺少足够文本层，需要 OCR 解析"
                    )
                if not indexes:
                    warnings.append(
                        f"第 {page.number + 1} 页无可提取文字，保留空页信息，请核对原件"
                    )
                start = (
                    paragraphs[indexes[0]].char_start
                    if indexes
                    else max(0, cursor - 1)
                )
                end = paragraphs[indexes[-1]].char_end if indexes else start
                pages.append(
                    PageNode(
                        page_no=page.number + 1,
                        width=page.rect.width,
                        height=page.rect.height,
                        text=page_text,
                        char_start=start,
                        char_end=end,
                        paragraph_indexes=indexes,
                        has_text_layer=bool(indexes),
                    )
                )
        full_text = "\n".join(p.text for p in paragraphs)
        if len(full_text.strip()) < 50:
            raise ParseBlocked("EMPTY_BODY", "未识别到至少 50 字符的有效 PDF 合同正文")
        warnings.append("按文本块读取；复杂分栏、表格及扫描/文本混排需人工核对，当前未还原表格结构")
        return ParsedDocument(
            doc_id=identifier,
            file_id=file_id,
            source_format="PDF_TEXT",
            channel="pdf_text",
            paragraphs=paragraphs,
            pages=pages,
            full_text=full_text,
            quality=QualityReport(
                # 文本字符区间可追溯，坐标精确到行/段落，不能声称字符级矩形。
                precision_cap="PARAGRAPH",
                has_text_layer=True,
                char_count=len(full_text),
                paragraph_count=len(paragraphs),
                page_count=len(pages),
                warnings=warnings,
            ),
            parse_duration_ms=int((perf_counter() - started) * 1000),
        )
    except ParseBlocked:
        raise
    except (pymupdf.FileDataError, pymupdf.EmptyFileError) as exc:
        raise ParseBlocked("ATTACHMENT_CORRUPT", "PDF 文件损坏或为空") from exc
    except Exception as exc:
        raise ParseBlocked("PARSE_ERROR", "PDF 解析出现异常，请检查原件") from exc
