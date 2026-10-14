"""内部调用的 ID 不合法时也必须在查库前拒绝。"""

from importlib import import_module

import pytest

node_module = import_module("app.graph.nodes.parse_document")


@pytest.mark.parametrize(
    "field,value",
    [("task_id", 0), ("file_id", True), ("file_id", 2**64), ("review_version", "1")],
)
def test_invalid_input_does_not_open_database(monkeypatch, field, value):
    def forbidden_factory():
        raise AssertionError("执行参数校验失败时不能打开数据库")

    monkeypatch.setattr(node_module, "get_session_factory", forbidden_factory)
    state = {"task_id": 1, "file_id": 1, "review_version": 1, field: value}
    with pytest.raises(ValueError, match=field):
        node_module.parse_document(state)
