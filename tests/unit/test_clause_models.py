"""条款结构必须能准确取回原文，编号和清洗正文不能混用。"""

import pytest
from pydantic import ValidationError

from app.parsing.ir import ParsedDocument
from app.structuring.models import Clause, ClauseType


@pytest.fixture
def document():
    first = "第一条 付款"
    second = "甲方应在验收后付款。"
    full_text = first + "\n" + second
    return ParsedDocument(
        doc_id="file-1",
        file_id=1,
        source_format="DOCX",
        channel="docx",
        paragraphs=[
            {
                "pid": "file-1:p0",
                "index": 0,
                "text": first,
                "char_start": 0,
                "char_end": len(first),
            },
            {
                "pid": "file-1:p1",
                "index": 1,
                "text": second,
                "char_start": len(first) + 1,
                "char_end": len(full_text),
            },
        ],
        full_text=full_text,
        quality={"precision_cap": "EXACT", "char_count": len(full_text), "paragraph_count": 2},
    )


@pytest.fixture
def clause_data(document):
    return {
        "index": 1,
        "clause_no": "第一条",
        "level": 1,
        "title": "付款",
        "text": "付款\n甲方应在验收后付款。",
        "paragraph_index_start": 0,
        "paragraph_index_end": 1,
        "char_start": 0,
        "char_end": len(document.full_text),
        "split_strategy": "NUMBER",
    }


def test_clause_keeps_numbered_source_separate_from_review_text(document, clause_data):
    clause = Clause.model_validate(clause_data)
    assert clause.validate_against_document(document) is clause
    assert clause.source_text(document) == document.full_text
    assert clause.source_text(document).startswith("第一条")
    assert clause.text.startswith("付款")
    assert len(clause.text) != clause.char_end - clause.char_start
    assert clause.clause_type == ClauseType.OTHER
    assert clause.is_cross_page() is False
    assert Clause.model_validate_json(clause.model_dump_json()) == clause


@pytest.mark.parametrize(
    "changes",
    [
        {"index": 0},
        {"text": "   "},
        {"char_end": 0},
        {"paragraph_index_start": 2},
        {"page_start": 1},
        {"page_start": 2, "page_end": 1},
        {"parent_index": 1},
        {"part_no": 1},
        {"type_confidence": 1.1},
        {"clause_type": "PAYMENT", "clause_type_secondary": "PAYMENT"},
    ],
)
def test_invalid_clause_fields_are_rejected(clause_data, changes):
    with pytest.raises(ValidationError):
        Clause.model_validate({**clause_data, **changes})


@pytest.mark.parametrize(
    "changes",
    [
        {"paragraph_index_end": 2},
        {"char_start": 1},
        {"char_end": 999},
        {"text": "与原文不符的付款内容"},
        {"clause_no": "第二条"},
        {"page_start": 1, "page_end": 1},
    ],
)
def test_clause_must_match_actual_document(document, clause_data, changes):
    clause = Clause.model_validate({**clause_data, **changes})
    with pytest.raises(ValueError):
        clause.validate_against_document(document)


def test_child_clause_can_cover_part_of_one_paragraph(document):
    start = document.paragraphs[1].char_start
    clause = Clause(
        index=2,
        level=0,
        text="甲方",
        paragraph_index_start=1,
        paragraph_index_end=1,
        char_start=start,
        char_end=start + 2,
        split_strategy="LENGTH_FALLBACK",
        parent_index=1,
        part_no=1,
    )
    assert clause.validate_against_document(document) is clause


def test_cross_page_marker(clause_data):
    clause = Clause.model_validate({**clause_data, "page_start": 1, "page_end": 2})
    assert clause.is_cross_page() is True
