"""统一解析结果（IR）：解析器输出它，条款切分与审查模块读取它。"""

from __future__ import annotations

import hashlib
import math
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IRModel(BaseModel):
    # 字段写错或坐标出现 NaN 时立即报错，避免错误数据流入定位与审查。
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SourceFormat(str, Enum):
    DOCX = "DOCX"
    PDF_TEXT = "PDF_TEXT"
    PDF_OCR = "PDF_OCR"
    IMAGE_OCR = "IMAGE_OCR"


class Precision(str, Enum):
    EXACT = "EXACT"  # 可以定位到字符区间。
    PARAGRAPH = "PARAGRAPH"
    PAGE = "PAGE"
    NONE = "NONE"


class BBox(IRModel):
    """PDF/OCR 的像素坐标，原点在左上角；DOCX 不填坐标。"""

    x0: float = Field(ge=0)
    y0: float = Field(ge=0)
    x1: float = Field(ge=0)
    y1: float = Field(ge=0)
    page_width: float = Field(gt=0)
    page_height: float = Field(gt=0)

    @model_validator(mode="after")
    def _check_bounds(self) -> BBox:
        if not (self.x0 < self.x1 <= self.page_width and self.y0 < self.y1 <= self.page_height):
            raise ValueError("坐标框必须有面积且位于页面范围内")
        return self

    def normalized(self) -> tuple[float, float, float, float]:
        """转换为 0～1 的比例，便于适配不同显示尺寸。"""
        return (
            self.x0 / self.page_width,
            self.y0 / self.page_height,
            self.x1 / self.page_width,
            self.y1 / self.page_height,
        )

    def scaled(self, render_width: float) -> tuple[float, float, float, float]:
        """按前端显示宽度等比例缩放坐标。"""
        if render_width <= 0 or not math.isfinite(render_width):
            raise ValueError("显示宽度必须为有限正数")
        factor = render_width / self.page_width
        return (self.x0 * factor, self.y0 * factor, self.x1 * factor, self.y1 * factor)


class RunSpan(IRModel):
    """Word 段落中的文字片段及样式，后续用来识别标题。"""

    text: str
    bold: bool | None = None  # 是否加粗
    italic: bool | None = None  # 是否斜体
    font_name: str | None = None  # 字体名称
    font_size_pt: float | None = Field(default=None, gt=0)  # 字号


class LineBox(IRModel):
    """PDF/OCR 的行坐标；字符位置相对于所在段落，不是完整正文。"""

    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    bbox: BBox

    @model_validator(mode="after")
    def _check_range(self) -> LineBox:
        if self.char_end <= self.char_start:
            raise ValueError("LineBox.char_end 必须大于 char_start")
        return self


class ParagraphNode(IRModel):
    """一个正文或表格段落；字符区间使用左闭右开 [start, end)。"""

    pid: str = Field(min_length=1)  # 服务端生成，例如 file-12:p0。
    index: int = Field(ge=0)
    page_no: int | None = Field(default=None, ge=1)
    text: str = Field(min_length=1)
    # 这两个位置相对于 ParsedDocument.full_text，供风险原文定位使用。
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    style_name: str | None = None
    heading_level: int | None = Field(default=None, ge=1, le=6)
    bbox: BBox | None = None
    line_boxes: list[LineBox] = Field(default_factory=list)
    runs: list[RunSpan] = Field(default_factory=list)
    in_table: bool | None = False  # PDF/OCR 无法判定是否来自表格时使用 None。
    table_id: str | None = None
    ocr_confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _check_range(self) -> ParagraphNode:
        if self.char_end - self.char_start != len(self.text):
            raise ValueError("段落字符区间长度必须等于 text 长度")
        return self


class PageNode(IRModel):
    """PDF/OCR 页信息；Word 不具有可靠页码，使用空 pages 列表。"""

    page_no: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    text: str
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    render_ref: str | None = None
    has_text_layer: bool = False
    paragraph_indexes: list[int] = Field(default_factory=list)


class QualityReport(IRModel):
    """解析质量信息；解析器填写，后续用于判断定位能力与降级。"""

    precision_cap: Precision
    has_text_layer: bool = False
    char_count: int = Field(default=0, ge=0)
    paragraph_count: int = Field(default=0, ge=0)
    page_count: int | None = Field(default=None, ge=0)
    ocr_engine: str | None = None
    ocr_avg_confidence: float | None = Field(default=None, ge=0, le=1)
    ocr_low_conf_ratio: float | None = Field(default=None, ge=0, le=1)
    empty_paragraph_ratio: float = Field(default=0, ge=0, le=1)
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)


class ParsedDocument(IRModel):
    """所有解析通道的统一输出；此模型本身不读取文件或访问数据库。"""

    doc_id: str = Field(min_length=1)
    file_id: int = Field(gt=0, le=2**64 - 1)
    source_format: SourceFormat
    channel: Literal["docx", "pdf_text", "ocr"]
    pages: list[PageNode] = Field(default_factory=list)
    paragraphs: list[ParagraphNode]
    full_text: str
    quality: QualityReport
    parse_duration_ms: int = Field(default=0, ge=0)

    def content_hash(self) -> str:
        """正文 SHA-256，用于比较解析结果是否一致。"""
        return hashlib.sha256(self.full_text.encode("utf-8")).hexdigest()

    def by_index(self, index: int) -> ParagraphNode | None:
        return self.paragraphs[index] if 0 <= index < len(self.paragraphs) else None

    def slice_text(self, char_start: int, char_end: int) -> str:
        """按全局字符位置取原文；拒绝负索引和越界，避免静默截取错误内容。"""
        if not 0 <= char_start <= char_end <= len(self.full_text):
            raise ValueError("原文字符区间越界")
        return self.full_text[char_start:char_end]

    @model_validator(mode="after")
    def _check_consistency(self) -> ParsedDocument:
        # IR-03：列表本身保持文档顺序，index 连续且从 0 开始。
        if [p.index for p in self.paragraphs] != list(range(len(self.paragraphs))):
            raise ValueError("IR-03：段落 index 必须按顺序连续且从 0 开始")

        # IR-01：正文严格来自段落拼接，段落之间只插入一个换行符。
        if "\n".join(p.text for p in self.paragraphs) != self.full_text:
            raise ValueError("IR-01：full_text 与段落拼接结果不一致")

        # IR-02：字符区间与拼接位置一致；换行占一个字符，最后不追加换行。
        cursor = 0
        for paragraph in self.paragraphs:
            if paragraph.char_start != cursor:
                raise ValueError("IR-02：段落字符区间与正文位置不一致")
            cursor = paragraph.char_end + 1

            # IR-04：行坐标中的局部字符区间不能越出所属段落。
            if any(line.char_end > len(paragraph.text) for line in paragraph.line_boxes):
                raise ValueError("IR-04：行字符区间越出所在段落")

        if self.source_format == SourceFormat.DOCX:
            if self.channel != "docx" or self.pages or self.quality.page_count is not None:
                raise ValueError("DOCX 必须使用 docx 通道且不填写页信息")
            if any(p.page_no is not None or p.bbox or p.line_boxes for p in self.paragraphs):
                raise ValueError("DOCX 段落不能填写页码或像素坐标")
        return self
