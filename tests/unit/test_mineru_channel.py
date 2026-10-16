"""验证 MinerU 协议、凭据隔离及 OCR 原文/坐标转换，不发送真实云请求。"""

import json
import zipfile
from io import BytesIO

import httpx
import pymupdf
import pytest
import respx

from app.core.config import Settings
from app.graph.nodes.structure_document import structure_document
from app.parsing import mineru_client
from app.parsing.exceptions import ParseBlocked
from app.parsing.ocr_channel import result_to_document
from app.structuring.anchor import Anchor


def result():
    texts = [
        "软件采购合同",
        "甲方：合成采购有限公司",
        "乙方：合成开发有限公司",
        "第一条 合同金额",
        "合同总金额：人民币1 0 0 0 0 0 . 0 0 元。甲方应在验收合格后支付价款。",
        "第二条 保密义务",
        "双方对商业秘密保密至合同终止后三年。",
    ]
    return {
        "items": [
            {
                "type": "text",
                "text": text,
                "bbox": [50, 40 + i * 60, 900, 80 + i * 60],
                "page_idx": 0,
            }
            for i, text in enumerate(texts)
        ],
        "page_sizes": [[595, 842]],
    }


def archive_bytes():
    content = result()
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("demo_content_list.json", json.dumps(content["items"]))
        archive.writestr(
            "layout.json", json.dumps({"pdf_info": [{"page_idx": 0, "page_size": [595, 842]}]})
        )
    return stream.getvalue()


@pytest.fixture
def context(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 70), "Synthetic image", fontsize=10)
        image = page.get_pixmap().tobytes("png")
        with pymupdf.open() as scan:
            page = scan.new_page()
            page.insert_image(page.rect, stream=image)
            scan.save(path)
    settings = Settings(
        _env_file=None,
        mysql_password="test",
        data_dir=tmp_path,
        mineru_enabled=True,
        mineru_api_token="test-token",
        mineru_base_url="https://mineru.example/api/v4",
        mineru_poll_interval_seconds=0.1,
    )
    monkeypatch.setattr(mineru_client, "get_settings", lambda: settings)
    monkeypatch.setattr(mineru_client.time, "sleep", lambda _: None)
    return path, settings


@respx.mock
def test_upload_poll_download_auth_isolation_and_cached_retry(context):
    path, _ = context
    root = "https://mineru.example/api/v4"
    respx.post(root + "/file-urls/batch").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {"batch_id": "test-batch", "file_urls": ["https://storage.example/upload"]},
            },
        )
    )
    upload = respx.put("https://storage.example/upload").mock(return_value=httpx.Response(200))
    respx.get(root + "/extract-results/batch/test-batch").mock(
        side_effect=[
            httpx.Response(
                200, json={"code": 0, "data": {"extract_result": [{"state": "pending"}]}}
            ),
            httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {"state": "done", "full_zip_url": "https://storage.example/result.zip"},
                        ]
                    },
                },
            ),
        ]
    )
    download = respx.get("https://storage.example/result.zip").mock(
        return_value=httpx.Response(200, content=archive_bytes())
    )
    raw = mineru_client.extract_with_mineru(path)
    assert raw == result()
    assert "authorization" not in upload.calls[0].request.headers
    assert "authorization" not in download.calls[0].request.headers
    assert mineru_client.extract_with_mineru(path) == raw
    assert upload.call_count == 1


def test_ocr_evidence_pages_and_fragmented_amount_are_not_fabricated(context):
    path, _ = context
    document = result_to_document(path, result(), file_id=7)
    assert document.source_format == "PDF_OCR" and document.quality.ocr_avg_confidence is None
    assert document.paragraphs[0].bbox.x0 == 595 * 50 / 1000
    anchor = Anchor.from_document_range(document, 0, len("软件采购合同"))
    assert anchor.page_no == 1 and anchor.bbox_list and anchor.precision == "PARAGRAPH"
    update = structure_document({"task_id": 1, "file_id": 7, "document": document})
    amount = update["metadata"].get("amount")
    assert amount.is_missing and any("OCR" in warning for warning in amount.warnings)
    assert "1 0 0 0 0 0" in document.full_text


def test_disabled_service_and_bad_result_are_explicitly_blocked(context):
    path, settings = context
    settings.mineru_enabled = False
    with pytest.raises(ParseBlocked) as error:
        mineru_client.extract_with_mineru(path)
    assert error.value.reason == "OCR_NOT_CONFIGURED"
    with pytest.raises(ParseBlocked) as error:
        mineru_client.read_result_zip(b"not a ZIP")
    assert error.value.reason == "OCR_RESULT_INVALID"
    bad = result()
    bad["items"][0]["page_idx"] = 5
    with pytest.raises(ParseBlocked) as error:
        result_to_document(path, bad, file_id=7)
    assert error.value.reason == "OCR_RESULT_INVALID"


def test_standalone_page_number_preserves_text_and_position(context):
    path, _ = context
    raw = result()
    raw["items"].append(
        {"type": "page_number", "text": "第 1 页", "page_idx": 0, "bbox": [450, 950, 550, 980]}
    )
    document = result_to_document(path, raw, file_id=7)
    page_number = document.paragraphs[-1]
    assert page_number.text == "第 1 页" and page_number.page_no == 1
    assert page_number.bbox.normalized() == pytest.approx((0.45, 0.95, 0.55, 0.98))
    assert document.pages[0].paragraph_indexes[-1] == page_number.index
    assert document.slice_text(page_number.char_start, page_number.char_end) == "第 1 页"


@pytest.mark.parametrize("mode,reason", [("http", "OCR_FAILED"), ("timeout", "OCR_TIMEOUT")])
@respx.mock
def test_service_failures_are_sanitized(context, mode, reason):
    path, _ = context
    route = respx.post("https://mineru.example/api/v4/file-urls/batch")
    if mode == "http":
        route.mock(return_value=httpx.Response(401, text="private upstream body"))
    else:
        route.mock(side_effect=httpx.ReadTimeout("private network detail"))
    with pytest.raises(ParseBlocked) as error:
        mineru_client.extract_with_mineru(path)
    assert error.value.reason == reason
    assert "private" not in error.value.detail
