"""真实数据库测试需要显式开启，避免默认测试依赖本机 MySQL。"""

import pytest


def pytest_addoption(parser):
    parser.addoption("--run-db-tests", action="store_true", help="运行本机 MySQL 集成测试")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-db-tests"):
        return
    skip = pytest.mark.skip(reason="加 --run-db-tests 才运行真实 MySQL 测试")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
