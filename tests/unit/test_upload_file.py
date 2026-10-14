"""在不连接 MySQL 的情况下验证文件拦截与临时文件清理。"""

from io import BytesIO
from types import SimpleNamespace

import pytest
from docx import Document

from app.api.tasks import _stage_upload
from app.core.errors import AppError


def make_docx() -> bytes:
    document = Document()
    document.add_paragraph("合同正文")
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def test_valid_docx_is_staged(tmp_path):
    content = make_docx()
    path, size, digest = _stage_upload(SimpleNamespace(file=BytesIO(content)), tmp_path, 50_000)
    assert path.read_bytes() == content
    assert size == len(content)
    assert len(digest) == 32


@pytest.mark.parametrize(
    ("content", "limit", "code"),
    [(b"not a docx" * 200, 50_000, 41501), (b"x" * 2048, 1024, 41301), (b"", 50_000, 40001)],
)
def test_rejected_file_leaves_no_temp_file(tmp_path, content, limit, code):
    with pytest.raises(AppError) as error:
        _stage_upload(SimpleNamespace(file=BytesIO(content)), tmp_path, limit)
    assert error.value.code == code
    assert list(tmp_path.iterdir()) == []
