"""验证正文与定位区间一致，错误数据不能流入后续审查。"""

import pytest
from pydantic import ValidationError

from app.parsing.ir import BBox, ParsedDocument


@pytest.fixture
def document_data():
    return {
        "doc_id": "file-12",
        "file_id": 12,
        "source_format": "DOCX",
        "channel": "docx",
        "paragraphs": [
            {"pid": "file-12:p0", "index": 0, "text": "甲方", "char_start": 0, "char_end": 2},
            {
                "pid": "file-12:p1",
                "index": 1,
                "text": "乙方",
                "char_start": 3,
                "char_end": 5,
                "in_table": True,
                "table_id": "table-0",
            },
        ],
        "full_text": "甲方\n乙方",
        "quality": {"precision_cap": "EXACT", "char_count": 5, "paragraph_count": 2},
    }


def test_valid_document_and_original_text_lookup(document_data):
    document = ParsedDocument.model_validate(document_data)
    assert document.by_index(1).in_table is True
    assert document.by_index(-1) is None
    assert document.by_index(2) is None
    assert document.slice_text(3, 5) == "乙方"
    assert document.pages == []
    assert all(p.page_no is None for p in document.paragraphs)
    restored = ParsedDocument.model_validate_json(document.model_dump_json())
    assert restored.content_hash() == document.content_hash()


def test_ir01_rejects_different_full_text(document_data):
    document_data["full_text"] = "甲方乙方"
    with pytest.raises(ValidationError, match="IR-01"):
        ParsedDocument.model_validate(document_data)


@pytest.mark.parametrize("start", [1, 2, 4])
def test_ir02_rejects_wrong_paragraph_position(document_data, start):
    # 保持段落长度正确，但制造与上一段重叠、遗漏换行或额外空洞。
    document_data["paragraphs"][1].update(char_start=start, char_end=start + 2)
    with pytest.raises(ValidationError, match="IR-02"):
        ParsedDocument.model_validate(document_data)


@pytest.mark.parametrize("index", [0, 2])
def test_ir03_rejects_duplicate_or_missing_index(document_data, index):
    document_data["paragraphs"][1]["index"] = index
    with pytest.raises(ValidationError, match="IR-03"):
        ParsedDocument.model_validate(document_data)


def test_ir04_rejects_line_outside_paragraph(document_data):
    document_data["paragraphs"][0]["line_boxes"] = [
        {
            "char_start": 0,
            "char_end": 3,
            "bbox": {"x0": 0, "y0": 0, "x1": 20, "y1": 10, "page_width": 100, "page_height": 200},
        }
    ]
    with pytest.raises(ValidationError, match="IR-04"):
        ParsedDocument.model_validate(document_data)


def test_rejects_wrong_paragraph_length(document_data):
    document_data["paragraphs"][0]["char_end"] = 1
    with pytest.raises(ValidationError, match="区间长度"):
        ParsedDocument.model_validate(document_data)


def test_docx_cannot_invent_page_numbers(document_data):
    document_data["paragraphs"][0]["page_no"] = 1
    with pytest.raises(ValidationError, match="DOCX 段落"):
        ParsedDocument.model_validate(document_data)


@pytest.mark.parametrize("start,end", [(-1, 2), (0, 6), (4, 3)])
def test_original_text_lookup_rejects_invalid_range(document_data, start, end):
    document = ParsedDocument.model_validate(document_data)
    with pytest.raises(ValueError, match="越界"):
        document.slice_text(start, end)


def test_coordinate_conversion():
    box = BBox(x0=10, y0=20, x1=30, y1=40, page_width=100, page_height=200)
    assert box.normalized() == (0.1, 0.1, 0.3, 0.2)
    assert box.scaled(50) == (5, 10, 15, 20)


@pytest.mark.parametrize("x1", [0, 101, float("nan")])
def test_rejects_invalid_coordinates(x1):
    with pytest.raises(ValidationError):
        BBox(x0=0, y0=0, x1=x1, y1=10, page_width=100, page_height=200)
