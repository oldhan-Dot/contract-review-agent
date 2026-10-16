import argparse
import asyncio
import json

import yaml
from sqlalchemy import text

from app.core.config import ROOT, get_settings
from app.core.db import get_engine
from app.health import health_report


def seed() -> None:
    users = yaml.safe_load((ROOT / "sql" / "seed" / "users.yaml").read_text(encoding="utf-8"))
    with get_engine().begin() as connection:
        for user in users:
            connection.execute(
                text("""
                    INSERT INTO app_user (username, display_name, role_code, company_name)
                    VALUES (:username, :display_name, :role_code, :company_name)
                    ON DUPLICATE KEY UPDATE display_name=VALUES(display_name),
                        role_code=VALUES(role_code), company_name=VALUES(company_name)
                """),
                user,
            )


def init_db() -> None:
    # The checked-in DDL contains no stored procedures or semicolons inside literals.
    for path in sorted((ROOT / "sql" / "ddl").glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
        with get_engine().begin() as connection:
            for statement in "\n".join(lines).split(";"):
                if statement.strip():
                    connection.exec_driver_sql(statement.strip())
    seed()
    with get_engine().connect() as connection:
        tables = connection.execute(text("SHOW TABLES")).all()
        users = connection.scalar(text("SELECT COUNT(*) FROM app_user"))
    print(f"数据库初始化完成：{len(tables)} 张表，{users} 个预置用户。")
    print("首批规则与示范文本由代码提供；模拟审批单在法务演示回写时自动关联。")


def main() -> None:
    parser = argparse.ArgumentParser(description="合同审查系统环境管理")
    parser.add_argument("command", choices=["init-db", "seed", "check-env"])
    args = parser.parse_args()
    try:
        settings = get_settings()
        if args.command in ("init-db", "seed"):
            if not settings.mysql_password.get_secret_value():
                raise RuntimeError("请先生成 .env 并配置 MYSQL_PASSWORD")
            init_db() if args.command == "init-db" else seed()
        else:
            report = asyncio.run(health_report())
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if report["status"] != "ok":
                raise SystemExit(1)
    except Exception:
        parser.exit(1, "环境操作失败：请检查 .env 配置、MySQL 状态及 DDL 文件。\n")


if __name__ == "__main__":
    main()
