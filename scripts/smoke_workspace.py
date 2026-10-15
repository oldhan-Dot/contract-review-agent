"""首版工作台联调：创建合成样例并检查真实接口，不替换模型调用。

先启动后端，再运行：.venv\Scripts\python.exe scripts/smoke_workspace.py
样例任务保留在工作台，方便查看；本脚本不会连接外部审批系统。
"""

import sys
import time

import httpx
import pymupdf


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
    with httpx.Client(base_url=base, timeout=15) as client:
        users = client.get("/api/demo/users").raise_for_status().json()["data"]

        def headers(role):
            user = next(user for user in users if user["role"] == role)
            return {"X-User-Id": str(user["id"]), "X-User-Role": role}

        biz, legal = headers("BIZ"), headers("LEGAL")
        capability = client.get("/api/workspace", headers=biz).raise_for_status().json()["data"]
        assert capability["runner"], "后台执行器未就绪"
        result = client.post("/api/demo/task", headers=biz).raise_for_status().json()["data"]
        task_id = result["task_id"]
        path = f"/api/tasks/{task_id}"
        for _ in range(90):
            detail = client.get(path, headers=legal).raise_for_status().json()["data"]
            if detail["task"]["status"] in {"blocked", "completed"}:
                break
            time.sleep(1)
        assert detail["task"]["status"] in {"blocked", "completed"}, "等待处理超时"
        assert detail["document"] and detail["clauses"] and detail["metadata"]
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
        print(
            f"PASS task={task_id}, status={detail['task']['status']}, "
            f"rules={sorted(codes)}, PDF={pages} pages"
        )
        print(
            "PASS permissions, legal draft, revision conflict, "
            "incomplete confirmation guard, reports"
        )


if __name__ == "__main__":
    main()
