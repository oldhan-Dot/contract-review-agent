"""DOCX 读取通道：保留正文与表格顺序，输出统一 ParsedDocument。"""

import re
from collections.abc import Iterator
from pathlib import Path
from time import perf_counter
from zipfile import BadZipFile

import msoffcrypto
from docx import Document
from docx.document import Document as DocxDocument
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from lxml.etree import XMLSyntaxError
from msoffcrypto.exceptions import FileFormatError, ParseError

from app.parsing.exceptions import ParseBlocked
from app.parsing.ir import (
    ParagraphNode,
    ParsedDocument,
    Precision,
    QualityReport,
    RunSpan,
    SourceFormat,
)

MIN_BODY_CHARACTERS = 50  # 技术方案 5.1.1：有效正文至少 50 字符。


def _load_document(path: Path) -> DocxDocument:
    try:
        # 加密的 DOCX 是 Office 加密容器，不能直接交给 python-docx 当作 ZIP 打开。
        with path.open("rb") as source:
            office_file = msoffcrypto.OfficeFile(source)
            if office_file.is_encrypted():
                raise ParseBlocked("ENCRYPTED_FILE", "文档已加密，请解除密码后重新上传")
        return Document(path)
    except FileNotFoundError as exc:
        raise ParseBlocked("ATTACHMENT_MISSING", "合同原件不存在") from exc
    except (
        OSError,
        BadZipFile,
        FileFormatError,
        ParseError,
        PackageNotFoundError,
        XMLSyntaxError,
        KeyError,
        ValueError,
    ) as exc:
        raise ParseBlocked("ATTACHMENT_CORRUPT", "合同原件无法读取或 DOCX 结构已损坏") from exc


def _iter_blocks(parent: DocxDocument | _Cell) -> Iterator[Paragraph | Table]:
    """遍历物理顺序，不能分别读取 paragraphs 和 tables 后再拼接。"""
    element = parent.element.body if isinstance(parent, DocxDocument) else parent._tc
    for child in element.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, parent)
        elif child.tag == qn("w:tbl"):
            yield Table(child, parent)


def _iter_paragraphs(document: DocxDocument, doc_id: str) -> Iterator[tuple[Paragraph, str | None]]:
    """表格逐行逐列读取，包含嵌套表格；合并单元格的文本只输出一次。"""
    table_number = 0

    def visit(parent, table_id=None):
        nonlocal table_number
        for block in _iter_blocks(parent):
            if isinstance(block, Paragraph):
                yield block, table_id
                continue
            current_table_id = f"{doc_id}:t{table_number}"
            table_number += 1
            # python-docx 在合并单元格时可能多次返回同一底层 XML 单元格。
            seen_cells = set()
            for row in block.rows:
                for cell in row.cells:
                    if cell._tc not in seen_cells:
                        seen_cells.add(cell._tc)
                        yield from visit(cell, current_table_id)

    yield from visit(document)


def _heading_level(style_name: str | None) -> int | None:
    match = re.fullmatch(r"(?:Heading|标题)\s*([1-6])", style_name or "", re.IGNORECASE)
    return int(match.group(1)) if match else None


def parse_docx(path: str | Path, *, file_id: int, doc_id: str | None = None) -> ParsedDocument:
    """纯文件读取入口；不写数据库、不修改任务状态、不调用模型服务。"""
    if isinstance(file_id, bool) or not isinstance(file_id, int) or not 0 < file_id < 2**64:
        raise ValueError("file_id 必须是数据库范围内的正整数")
    if doc_id is not None and not doc_id.strip():
        raise ValueError("doc_id 不能为空")
    started = perf_counter()
    try:
        document = _load_document(Path(path))
        identifier = doc_id if doc_id is not None else f"file-{file_id}"
        paragraphs = []
        cursor = 0
        total_count = empty_count = empty_heading_count = 0
        for paragraph, table_id in _iter_paragraphs(document, identifier):
            total_count += 1
            text = paragraph.text
            style_name = paragraph.style.name if paragraph.style is not None else None
            heading_level = _heading_level(style_name)
            if not text.strip():
                empty_count += 1
                if heading_level is not None:
                    empty_heading_count += 1
                continue

            # 不 strip 正文、不添加缩进符，确保保存的文字与原文字符位置一致。
            index = len(paragraphs)
            runs = [
                RunSpan(
                    text=run.text,
                    bold=run.bold,
                    italic=run.italic,
                    font_name=run.font.name,
                    font_size_pt=run.font.size.pt if run.font.size is not None else None,
                )
                for run in paragraph.runs
            ]
            node = ParagraphNode(
                pid=f"{identifier}:p{index}",
                index=index,
                text=text,
                char_start=cursor,
                char_end=cursor + len(text),
                style_name=style_name,
                heading_level=heading_level,
                runs=runs,
                in_table=table_id is not None,
                table_id=table_id,
            )
            paragraphs.append(node)
            cursor = node.char_end + 1  # 为下一段之间的换行预留一个字符。

        full_text = "\n".join(p.text for p in paragraphs)
        if not paragraphs or len(full_text.strip()) < MIN_BODY_CHARACTERS:
            raise ParseBlocked("EMPTY_BODY", "未识别到至少 50 字符的有效合同正文")
        warnings = []
        if empty_heading_count:
            warnings.append(f"跳过 {empty_heading_count} 个空标题段落，不生成零长度文本区间")
        return ParsedDocument(
            doc_id=identifier,
            file_id=file_id,
            source_format=SourceFormat.DOCX,
            channel="docx",
            paragraphs=paragraphs,
            full_text=full_text,
            quality=QualityReport(
                precision_cap=Precision.EXACT,
                char_count=len(full_text),
                paragraph_count=len(paragraphs),
                empty_paragraph_ratio=empty_count / total_count if total_count else 0,
                warnings=warnings,
            ),
            parse_duration_ms=int((perf_counter() - started) * 1000),
        )
    except ParseBlocked:
        raise
    except Exception as exc:
        # 统一封装未预期的库异常；对外信息不包含合同正文或底层堆栈。
        raise ParseBlocked("PARSE_ERROR", "DOCX 解析出现异常") from exc
