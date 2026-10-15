"""用实际生成的 PDF 检查页码、坐标、结构化和必须停止的异常场景。"""

import pymupdf
import pytest

from app.graph.nodes.structure_document import structure_document
from app.parsing.exceptions import ParseBlocked
from app.parsing.pdf_channel import parse_pdf
from app.structuring.anchor import Anchor


def write_pdf(path, *, encrypted=False, scanned=False):
    with pymupdf.open() as pdf:
        for lines in [
            [
                "软件采购合同",
                "甲方：合成采购有限公司",
                "乙方：合成开发有限公司",
                "第一条 金额与付款",
                "合同总金额：人民币100000.00元。甲方应在验收合格后支付价款。",
            ],
            [
                "第二条 保密义务",
                "双方对商业秘密保密至合同终止后三年。",
                "第三条 不可抗力",
                "受影响方应在事件发生后五个工作日内书面通知对方。",
            ],
        ]:
            page = pdf.new_page()
            for i, text in enumerate(lines):
                page.insert_text((40, 60 + 35 * i), text, fontname="china-s", fontsize=10)
        if scanned:
            image = pdf[0].get_pixmap().tobytes("png")
            # 使用新文档放入图像，确保没有原页文字层。
            with pymupdf.open() as scan:
                page = scan.new_page()
                page.insert_image(page.rect, stream=image)
                scan.save(path)
            return
        options = (
            {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": "test"} if encrypted else {}
        )
        pdf.save(path, **options)


def test_pdf_pages_coordinates_and_existing_structure_pipeline(tmp_path):
    path = tmp_path / "contract.pdf"
    write_pdf(path)
    document = parse_pdf(path, file_id=7)
    assert document.source_format == "PDF_TEXT" and len(document.pages) == 2
    for page in document.pages:
        assert document.full_text[page.char_start : page.char_end] == page.text
        assert all(document.paragraphs[i].page_no == page.page_no for i in page.paragraph_indexes)
    for paragraph in document.paragraphs:
        assert paragraph.line_boxes and paragraph.bbox
        for line in paragraph.line_boxes:
            assert paragraph.text[line.char_start : line.char_end].strip()
            assert all(0 <= value <= 1 for value in line.bbox.normalized())
    start = document.full_text.index("100000.00")
    anchor = Anchor.from_document_range(document, start, start + len("100000.00"))
    assert anchor.page_no == 1 and anchor.bbox_list and anchor.precision == "PARAGRAPH"
    anchor.validate_against_document(document)
    cross_page = Anchor.from_document_range(
        document,
        document.paragraphs[0].char_start,
        document.paragraphs[-1].char_end,
    )
    assert cross_page.page_no == 1 and cross_page.page_no_end == 2 and not cross_page.bbox_list
    result = structure_document(
        {"task_id": 1, "file_id": 7, "review_version": 1, "document": document}
    )
    assert result["clauses"] and result["metadata"].get("amount").field_value == "100000.00"
    assert result["metadata"].get("party_a_name").anchor.page_no == 1


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("encrypted", "ENCRYPTED_FILE"),
        ("scanned", "OCR_REQUIRED"),
        ("corrupt", "ATTACHMENT_CORRUPT"),
        ("blank", "EMPTY_BODY"),
    ],
)
def test_unreadable_pdf_is_not_a_complete_text_document(tmp_path, kind, reason):
    path = tmp_path / "contract.pdf"
    if kind in {"encrypted", "scanned"}:
        write_pdf(path, **{kind: True})
    elif kind == "corrupt":
        path.write_bytes(b"not a PDF")
    else:
        with pymupdf.open() as pdf:
            pdf.new_page()
            pdf.save(path)
    with pytest.raises(ParseBlocked) as error:
        parse_pdf(path, file_id=7)
    assert error.value.reason == reason


def test_rotation_is_applied_to_page_coordinates(tmp_path):
    path = tmp_path / "rotated.pdf"
    write_pdf(path)
    with pymupdf.open(path) as pdf:
        pdf[0].set_rotation(90)
        data = pdf.tobytes()
    path.write_bytes(data)
    document = parse_pdf(path, file_id=7)
    assert document.pages[0].width > document.pages[0].height
    assert document.paragraphs[0].bbox.page_width == document.pages[0].width
