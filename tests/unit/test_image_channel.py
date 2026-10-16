"""单图格式、方向和 OCR/证据流水线；不调用云服务。"""

import struct
from types import SimpleNamespace

import pymupdf
import pytest

from app.graph.nodes.structure_document import structure_document
from app.parsing import image_channel
from app.parsing.exceptions import ParseBlocked
from app.structuring.anchor import Anchor


def image_bytes(extension):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=300, height=400)
        page.insert_text((20, 50), "Synthetic contract image", fontsize=10)
        return page.get_pixmap().tobytes(extension)


@pytest.mark.parametrize("extension", ["png", "jpeg"])
def test_image_reuses_ocr_structure_and_anchors(tmp_path, monkeypatch, extension):
    path = tmp_path / f"contract.{extension}"
    path.write_bytes(image_bytes(extension))
    monkeypatch.setattr(
        image_channel, "get_settings", lambda: SimpleNamespace(storage_path=tmp_path)
    )

    def cloud_result(normalized):
        with pymupdf.open(normalized) as pdf:
            page = pdf[0]
            return {
                "page_sizes": [[page.rect.width, page.rect.height]],
                "items": [
                    {
                        "type": "text",
                        "text": text,
                        "page_idx": 0,
                        "bbox": [50, 50 + 90 * i, 950, 110 + 90 * i],
                    }
                    for i, text in enumerate(
                        [
                            "软件采购合同",
                            "甲方：合成采购有限公司",
                            "乙方：合成开发有限公司",
                            "第一条 合同金额",
                            "合同总金额：人民币100000.00元，甲方在验收合格后支付价款。",
                            "第二条 保密义务",
                            "双方对商业秘密保密至合同终止后三年。",
                        ]
                    )
                ],
            }

    monkeypatch.setattr(image_channel, "extract_with_mineru", cloud_result)
    original = path.read_bytes()
    document = image_channel.parse_image(path, file_id=12)
    assert document.source_format == "IMAGE_OCR" and document.channel == "ocr"
    assert len(document.pages) == 1 and path.read_bytes() == original
    anchor = Anchor.from_document_range(document, 0, len("软件采购合同"))
    assert anchor.page_no == 1 and anchor.bbox_list and anchor.precision == "PARAGRAPH"
    update = structure_document({"task_id": 1, "file_id": 12, "document": document})
    assert update["metadata"].get("amount").field_value == "100000.00"


def test_exif_rotated_jpeg_uses_the_same_orientation_for_ocr_and_preview():
    jpeg = image_bytes("jpeg")
    # 给测试 JPEG 添加 EXIF orientation=6（顺时针 90 度）。
    tiff = (
        b"II"
        + struct.pack("<HIH", 42, 8, 1)
        + struct.pack("<HHI", 0x112, 3, 1)
        + struct.pack("<H", 6)
        + b"\0\0"
        + struct.pack("<I", 0)
    )
    exif = b"Exif\0\0" + tiff
    rotated = jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + jpeg[2:]
    with pymupdf.open(stream=image_channel.image_as_pdf(rotated, "jpeg"), filetype="pdf") as pdf:
        assert pdf[0].rect.width > pdf[0].rect.height


def test_fake_format_and_excessive_pixels_are_rejected_before_ocr():
    with pytest.raises(ParseBlocked) as error:
        image_channel.image_as_pdf(image_bytes("png"), "jpg")
    assert error.value.reason == "ATTACHMENT_CORRUPT"
    huge = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 10000, 10000)
    with pytest.raises(ParseBlocked) as error:
        image_channel.image_as_pdf(huge, "png")
    assert error.value.reason == "IMAGE_TOO_LARGE"
