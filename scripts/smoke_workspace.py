"""首版工作台联调：创建合成样例并检查真实接口，不替换模型调用。

先启动后端，再运行：.venv\Scripts\python.exe scripts/smoke_workspace.py
样例任务保留在工作台，方便查看；本脚本不会连接外部审批系统。
"""

import argparse
import time

import httpx
import pymupdf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", nargs="?", default="http://127.0.0.1:8001")
    parser.add_argument("--task-id", type=int, help="复用已生成的合成演示任务")
    parser.add_argument(
        "--require-complete", action="store_true", help="要求真实审查成功并验证模拟回写"
    )
    args = parser.parse_args()
    base = args.base
    with httpx.Client(base_url=base, timeout=15) as client:
        users = client.get("/api/demo/users").raise_for_status().json()["data"]

        def headers(role):
            user = next(user for user in users if user["role"] == role)
            return {"X-User-Id": str(user["id"]), "X-User-Role": role}

        biz, legal = headers("BIZ"), headers("LEGAL")
        capability = client.get("/api/workspace", headers=biz).raise_for_status().json()["data"]
        assert capability["runner"], "后台执行器未就绪"
        if args.task_id:
            task_id = args.task_id
        else:
            result = client.post("/api/demo/task", headers=biz).raise_for_status().json()["data"]
            task_id = result["task_id"]
        path = f"/api/tasks/{task_id}"
        for _ in range(120):
            detail = client.get(path, headers=legal).raise_for_status().json()["data"]
            if detail["task"]["status"] in {"blocked", "completed"}:
                break
            time.sleep(1)
        assert detail["task"]["status"] in {"blocked", "completed"}, "等待处理超时"
        assert detail["document"] and detail["clauses"] and detail["metadata"]
        # 完整链路会自动保存/确认意见，只允许在程序生成的合成演示任务上执行。
        assert "合成演示" in detail["task"]["contract_name"]
        assert "本文件为合成演示数据，不是真实业务合同。" in detail["document"]["full_text"]
        if args.require_complete:
            assert detail["complete"] and detail["task"]["status"] == "completed", (
                f"真实模型审查未通过：{detail['task']['block_detail']}"
            )
            assert any(risk["source"] == "llm" for risk in detail["risks"]), "样例应有模型语义风险"
        codes = {item["rule_code"] for item in detail["risks"]}
        assert {"R-AMT-01", "R-LIA-01"} <= codes, codes
        assert not {"R-SUB-01", "R-CON-01", "R-FOR-01", "R-MIS-03"} & codes, codes
        if not capability["llm_configured"]:
            assert detail["task"]["block_reason"] == "AI_NOT_CONFIGURED"
            assert not detail["complete"]
        body = {
            "review_version": detail["task"]["review_version"],
            "expected_revision": detail["revision"],
            "comment": detail["human_review"].get("comment", "")
            or "合成样例联调草稿，待法务复核。",
        }
        assert client.patch(path + "/review", headers=biz, json=body).status_code == 403
        saved = client.patch(path + "/review", headers=legal, json=body).raise_for_status()
        revision = saved.json()["data"]["revision"]
        assert client.patch(path + "/review", headers=legal, json=body).status_code == 409
        if not detail["complete"]:
            body.update(expected_revision=revision, confirm=True)
            assert client.patch(path + "/review", headers=legal, json=body).status_code == 409
        elif args.require_complete:
            body.update(
                expected_revision=revision,
                confirm=True,
                comment="自动联调：合成合同功能验证，不代表真实合同的人工法律意见。",
            )
            confirmed = client.patch(path + "/review", headers=legal, json=body).raise_for_status()
            revision = confirmed.json()["data"]["revision"]
            assert confirmed.json()["data"]["confirmed"]
            client.post(path + "/mock-approval", headers=legal).raise_for_status()
            write_body = {"review_version": body["review_version"], "expected_revision": revision}
            first = client.post(
                path + "/writeback", headers=legal, json=write_body
            ).raise_for_status()
            second = client.post(
                path + "/writeback", headers=legal, json=write_body
            ).raise_for_status()
            assert first.json()["data"]["comment_id"] == second.json()["data"]["comment_id"]
            comments = (
                client.get(path + "/comments", headers=legal).raise_for_status().json()["data"]
            )
            assert sum(row["id"] == first.json()["data"]["comment_id"] for row in comments) == 1
            print("PASS synthetic confirmation, mock writeback, idempotent replay")
        markdown = client.get(path + "/report", headers=legal).raise_for_status().json()["data"]
        assert "风险与建议" in markdown["markdown"]
        pdf = client.get(
            path + "/report?format=pdf&download=true", headers=legal
        ).raise_for_status()
        with pymupdf.open(stream=pdf.content, filetype="pdf") as document:
            assert len(document) >= 1 and "审查报告" in "".join(
                page.get_text() for page in document
            )
            pages = len(document)
        assert client.get("/api/rules", headers=biz).status_code == 403
        rules = client.get("/api/rules", headers=legal).raise_for_status().json()["data"]
        assert len(rules) == 7
        assert client.get("/api/tasks", headers=biz).raise_for_status().json()["data"]["total"] > 0
        print(f"PASS task={task_id}, status={detail['task']['status']}, PDF={pages} pages")
        print("Rule codes:", sorted(code for code in codes if code))
        print(
            "PASS permissions, legal draft, revision conflict, "
            "incomplete confirmation guard, reports"
        )


if __name__ == "__main__":
    main()
