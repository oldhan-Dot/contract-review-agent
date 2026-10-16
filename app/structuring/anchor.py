"""原文位置数据结构；字段和风险项共用，当前先提供 DOCX 位置构造。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.parsing.ir import BBox, ParsedDocument, Precision, SourceFormat


class Anchor(BaseModel):
    """记录一段证据在原件中的位置，不保存数据库会话或文件句柄。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    anchor_type: Literal["clause", "metadata"]
    file_id: int = Field(gt=0, le=2**64 - 1)
    page_no: int | None = Field(default=None, ge=1)
    page_no_end: int | None = Field(default=None, ge=1)
    paragraph_index: int | None = Field(default=None, ge=0)
    paragraph_index_end: int | None = Field(default=None, ge=0)
    # 局部偏移分别相对于起始、结束段落；全局偏移相对于 full_text。
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    global_char_start: int = Field(ge=0)
    global_char_end: int = Field(ge=0)
    bbox: BBox | None = None
    bbox_list: list[BBox] = Field(default_factory=list)
    precision: Precision
    matched_text: str = Field(min_length=1, max_length=500)
    matched_text_truncated: bool = False
    match_method: Literal["STRUCT", "SEARCH", "OCR_BOX"]

    @model_validator(mode="after")
    def _check_structure(self) -> Anchor:
        if self.global_char_end <= self.global_char_start:
            raise ValueError("原文全局字符区间必须非空")
        for start, end, label in (
            (self.page_no, self.page_no_end, "页码"),
            (self.paragraph_index, self.paragraph_index_end, "段落"),
        ):
            if end is not None and (start is None or end < start):
                raise ValueError(f"原文结束{label}必须有对应起点，且不能早于起点")
        if (self.char_start is None) != (self.char_end is None):
            raise ValueError("原文局部字符区间必须同时填写起止位置")
        if (
            self.precision in (Precision.EXACT, Precision.PARAGRAPH)
            and self.paragraph_index is None
        ):
            raise ValueError("字符或段落定位必须有段落编号")
        if self.precision == Precision.EXACT and self.char_start is None:
            raise ValueError("字符定位必须有局部字符区间")
        if self.precision == Precision.PAGE and self.page_no is None:
            raise ValueError("页级定位必须有页码")
        end_paragraph = (
            self.paragraph_index_end
            if self.paragraph_index_end is not None
            else self.paragraph_index
        )
        if (
            self.char_start is not None
            and end_paragraph == self.paragraph_index
            and self.char_end <= self.char_start
        ):
            raise ValueError("同一段落中的字符区间必须非空")
        if self.matched_text_truncated and len(self.matched_text) != 500:
            raise ValueError("截断证据必须保留原文首尾各 250 字符")
        return self

    def char_len(self) -> int:
        return self.global_char_end - self.global_char_start

    def validate_against_document(self, document: ParsedDocument) -> Anchor:
        """需要实际原文时显式调用，防止编造证据、串文件或偏移错位。"""
        if self.file_id != document.file_id:
            raise ValueError("原文位置与文档的文件 ID 不一致")
        actual = document.slice_text(self.global_char_start, self.global_char_end)
        expected = actual[:250] + actual[-250:] if self.matched_text_truncated else actual
        if self.matched_text_truncated and len(actual) <= 500:
            raise ValueError("原文未超过 500 字符，不应标记截断")
        if expected != self.matched_text:
            raise ValueError("证据文字与指定位置的原文不一致")
        ranks = {Precision.NONE: 0, Precision.PAGE: 1, Precision.PARAGRAPH: 2, Precision.EXACT: 3}
        if ranks[self.precision] > ranks[document.quality.precision_cap]:
            raise ValueError("定位精度超过文档解析结果的能力")
        known_pages = {p.page_no for p in document.paragraphs if p.page_no is not None}
        known_pages.update(page.page_no for page in document.pages)
        if any(
            page is not None and page not in known_pages
            for page in (self.page_no, self.page_no_end)
        ):
            raise ValueError("原文位置引用的页码不存在")

        if self.paragraph_index is not None:
            first = document.by_index(self.paragraph_index)
            last_index = (
                self.paragraph_index_end
                if self.paragraph_index_end is not None
                else self.paragraph_index
            )
            last = document.by_index(last_index)
            if first is None or last is None:
                raise ValueError("原文位置引用的段落不存在")
            if not first.char_start <= self.global_char_start < first.char_end:
                raise ValueError("原文全局起点与起始段落不一致")
            if not last.char_start < self.global_char_end <= last.char_end:
                raise ValueError("原文全局终点与结束段落不一致")
            if self.char_start is not None and (
                self.char_start != self.global_char_start - first.char_start
                or self.char_end != self.global_char_end - last.char_start
            ):
                raise ValueError("原文局部位置与全局位置不一致")
            if self.page_no != first.page_no or (self.page_no_end or self.page_no) != last.page_no:
                raise ValueError("原文位置的页码与段落不一致")
        if document.source_format == SourceFormat.DOCX and (
            self.page_no is not None or self.page_no_end is not None or self.bbox or self.bbox_list
        ):
            raise ValueError("DOCX 原文位置不能包含页码或像素坐标")
        return self

    @classmethod
    def from_document_range(
        cls,
        document: ParsedDocument,
        start: int,
        end: int,
        *,
        anchor_type: Literal["clause", "metadata"] = "metadata",
    ) -> Anchor:
        """按已知文字区间构造 DOCX/PDF 证据；PDF 坐标只声明行/段落精度。"""
        if document.source_format not in (
            SourceFormat.DOCX,
            SourceFormat.PDF_TEXT,
            SourceFormat.PDF_OCR,
            SourceFormat.IMAGE_OCR,
        ):
            raise ValueError("当前原文位置构造支持 DOCX、文本 PDF、扫描 PDF 和图片 OCR 解析结果")
        text = document.slice_text(start, end)
        if not text:
            raise ValueError("原文证据区间不能为空")
        first = next((p for p in document.paragraphs if p.char_start <= start < p.char_end), None)
        last = next((p for p in document.paragraphs if p.char_start < end <= p.char_end), None)
        if first is None or last is None:
            raise ValueError("原文区间起止位置必须位于实际段落内")
        pdf = document.source_format in (
            SourceFormat.PDF_TEXT,
            SourceFormat.PDF_OCR,
            SourceFormat.IMAGE_OCR,
        )
        boxes = []
        # 跨页不把多页矩形合成一个框；保留起止页/段落，后续预览按段落取各页行框。
        if pdf and first.page_no == last.page_no:
            for paragraph in document.paragraphs[first.index : last.index + 1]:
                boxes.extend(
                    line.bbox
                    for line in paragraph.line_boxes
                    if (
                        paragraph.char_start + line.char_start < end
                        and paragraph.char_start + line.char_end > start
                    )
                )
        anchor = cls(
            anchor_type=anchor_type,
            file_id=document.file_id,
            page_no=first.page_no,
            page_no_end=last.page_no,
            bbox_list=boxes,
            paragraph_index=first.index,
            paragraph_index_end=last.index,
            char_start=start - first.char_start,
            char_end=end - last.char_start,
            global_char_start=start,
            global_char_end=end,
            precision=Precision.PARAGRAPH if pdf else Precision.EXACT,
            matched_text=text if len(text) <= 500 else text[:250] + text[-250:],
            matched_text_truncated=len(text) > 500,
            match_method="OCR_BOX" if document.channel == "ocr" else "STRUCT",
        )
        return anchor.validate_against_document(document)
