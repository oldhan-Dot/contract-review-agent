"""将实际验证的 MinerU 内容列表转换为带位置证据的统一 IR。"""

from html.parser import HTMLParser
from pathlib import Path
from time import perf_counter

import pymupdf

from app.parsing.exceptions import ParseBlocked
from app.parsing.ir import BBox, LineBox, PageNode, ParagraphNode, ParsedDocument, QualityReport
from app.parsing.mineru_client import extract_with_mineru


class _TableText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"}:
            self.parts.append("\t")
        elif tag == "tr":
            self.parts.append("\n")


def _text(item):
    kind = item["type"]
    if kind == "table":
        if not item.get("table_body"):
            raise ValueError("Table body missing")
        parser = _TableText()
        parser.feed(item["table_body"])
        return "".join(parser.parts).strip()
    if kind == "list":
        return "\n".join(item["list_items"])
    if kind == "image":
        return "\n".join(item.get("image_caption", []))
    # MinerU 会把独立页码标记为 page_number，其文字和坐标沿用普通内容块协议。
    if kind in {
        "text",
        "equation",
        "header",
        "footer",
        "page_header",
        "page_footer",
        "page_number",
    }:
        return item.get("text", "")
    raise ValueError("Unsupported content type")


def result_to_document(path: Path, raw: dict, *, file_id: int) -> ParsedDocument:
    """保留 OCR 原始文字；内容框为归一化 0～1000，不能冒充字符/行级坐标。"""
    paragraphs, pages = [], []
    warnings = [
        "本合同正文来自 MinerU OCR，识别可能有错字、漏字和数字空格，请逐项对照原件复核。",
        "当前返回协议未提供可靠 OCR 置信度，不虚构分数；定位精度为内容块。",
    ]
    cursor = 0
    try:
        with pymupdf.open(path) as pdf:
            if len(raw["page_sizes"]) != len(pdf):
                raise ValueError("OCR page count differs from original")
            grouped = [[] for _ in pdf]
            for item in raw["items"]:
                index = item["page_idx"]
                if type(index) is not int or not 0 <= index < len(pdf):
                    raise ValueError("Invalid OCR page")
                grouped[index].append(item)
            for page in pdf:
                width, height = page.rect.width, page.rect.height
                expected = raw["page_sizes"][page.number]
                if abs(expected[0] - width) > 2 or abs(expected[1] - height) > 2:
                    raise ValueError("OCR page dimensions differ from original")
                indexes = []
                for item in grouped[page.number]:
                    text = _text(item).strip()
                    if item["type"] == "image":
                        warnings.append(
                            f"第 {page.number + 1} 页含图像区域，图像内文字完整性需核对"
                        )
                    if not text:
                        continue
                    x0, y0, x1, y1 = item["bbox"]
                    if not 0 <= x0 < x1 <= 1000 or not 0 <= y0 < y1 <= 1000:
                        raise ValueError("Invalid normalized OCR box")
                    box = BBox(
                        x0=x0 * width / 1000,
                        y0=y0 * height / 1000,
                        x1=x1 * width / 1000,
                        y1=y1 * height / 1000,
                        page_width=width,
                        page_height=height,
                    )
                    index = len(paragraphs)
                    level = item.get("text_level")
                    table = item["type"] == "table"
                    paragraphs.append(
                        ParagraphNode(
                            pid=f"file-{file_id}:p{index}",
                            index=index,
                            page_no=page.number + 1,
                            text=text,
                            char_start=cursor,
                            char_end=cursor + len(text),
                            bbox=box,
                            # 沿用行框字段承载整个内容块，质量提示明确只到块级。
                            line_boxes=[LineBox(char_start=0, char_end=len(text), bbox=box)],
                            heading_level=level if type(level) is int and 1 <= level <= 6 else None,
                            in_table=True if table else None,
                            table_id=f"file-{file_id}:t{index}" if table else None,
                        )
                    )
                    indexes.append(index)
                    cursor += len(text) + 1
                if not indexes and page.get_image_info():
                    raise ValueError("Image page produced no readable OCR")
                if not indexes:
                    warnings.append(f"第 {page.number + 1} 页未识别到文字，请核对空页")
                start = paragraphs[indexes[0]].char_start if indexes else max(0, cursor - 1)
                end = paragraphs[indexes[-1]].char_end if indexes else start
                pages.append(
                    PageNode(
                        page_no=page.number + 1,
                        width=width,
                        height=height,
                        text="\n".join(paragraphs[i].text for i in indexes),
                        char_start=start,
                        char_end=end,
                        paragraph_indexes=indexes,
                        has_text_layer=False,
                    )
                )
        text = "\n".join(p.text for p in paragraphs)
        if len(text.strip()) < 50:
            raise ParseBlocked("EMPTY_BODY", "OCR 未识别到至少 50 字符的有效正文")
        return ParsedDocument(
            doc_id=f"file-{file_id}",
            file_id=file_id,
            source_format="PDF_OCR",
            channel="ocr",
            paragraphs=paragraphs,
            pages=pages,
            full_text=text,
            quality=QualityReport(
                precision_cap="PARAGRAPH",
                has_text_layer=False,
                ocr_engine="MinerU-v4-vlm",
                char_count=len(text),
                paragraph_count=len(paragraphs),
                page_count=len(pages),
                warnings=list(dict.fromkeys(warnings)),
            ),
        )
    except ParseBlocked:
        raise
    except Exception:
        raise ParseBlocked(
            "OCR_RESULT_INVALID", "OCR 文字、页码或位置无法与原件对应，不能继续审查"
        ) from None


def parse_scanned_pdf(path: str | Path, *, file_id: int) -> ParsedDocument:
    """扫描 PDF 使用云 OCR，后续结构化和审查复用同一数据格式。"""
    started = perf_counter()
    document = result_to_document(Path(path), extract_with_mineru(Path(path)), file_id=file_id)
    document.parse_duration_ms = int((perf_counter() - started) * 1000)
    return document
