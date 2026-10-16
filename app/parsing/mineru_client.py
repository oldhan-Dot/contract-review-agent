"""MinerU 云 API v4：申请上传地址、上传原件、轮询、读取受限 ZIP 结果。"""

import hashlib
import json
import time
import zipfile
from io import BytesIO
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.parsing.exceptions import ParseBlocked

MAX_ZIP_BYTES = 30 * 1024 * 1024


def _remote_url(value):
    url = httpx.URL(value)
    if url.scheme != "https" or not url.host or url.username or url.password:
        raise ParseBlocked("OCR_RESULT_INVALID", "MinerU 返回的资源地址不合法")
    return str(url)


def read_result_zip(content: bytes) -> dict:
    """只读取已知 JSON 成员，不将压缩包解压到文件系统。"""
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > 2000 or sum(item.file_size for item in members) > 100 * 1024 * 1024:
                raise ValueError("ZIP exceeds limit")
            names = [
                item.filename for item in members if item.filename.endswith("_content_list.json")
            ]
            layouts = [
                item.filename for item in members if Path(item.filename).name == "layout.json"
            ]
            if len(names) != 1 or len(layouts) != 1:
                raise ValueError("Unknown ZIP schema")
            for name in (names[0], layouts[0]):
                if archive.getinfo(name).file_size > 20 * 1024 * 1024:
                    raise ValueError("JSON exceeds limit")
            items = json.loads(archive.read(names[0]))
            layout = json.loads(archive.read(layouts[0]))
            sizes = [page["page_size"] for page in layout["pdf_info"]]
            if [page["page_idx"] for page in layout["pdf_info"]] != list(range(len(sizes))):
                raise ValueError("Invalid page sequence")
            if not isinstance(items, list):
                raise ValueError("Invalid content list")
            return {"items": items, "page_sizes": sizes}
    except Exception:
        raise ParseBlocked(
            "OCR_RESULT_INVALID", "MinerU 结果包结构不符合已验证协议，不能继续审查"
        ) from None


def _write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def extract_with_mineru(path: Path) -> dict:
    """仅向已配置云服务发送当前待解析文件；凭据不发送至上传/下载域名。"""
    settings = get_settings()
    if not settings.mineru_enabled or not settings.mineru_api_token.get_secret_value():
        raise ParseBlocked("OCR_NOT_CONFIGURED", "扫描件需要 MinerU，请启用解析服务并配置 Token")
    source = path.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    cache = settings.storage_path / "renders" / "mineru"
    cache.mkdir(parents=True, exist_ok=True)
    result_file, job_file = cache / f"{digest}-v1.json", cache / f"{digest}-job.json"
    if result_file.is_file():
        try:
            return json.loads(result_file.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raise ParseBlocked(
                "OCR_RESULT_INVALID", "OCR 本地结果缓存无法读取，请由管理员检查"
            ) from None
    root = settings.mineru_base_url.rstrip("/")
    headers = {"Authorization": "Bearer " + settings.mineru_api_token.get_secret_value()}
    deadline = time.monotonic() + settings.mineru_task_timeout_seconds

    def timeout():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ParseBlocked(
                "OCR_TIMEOUT", "MinerU 等待超时；已保留任务编号，管理员可重试继续查询"
            )
        return min(settings.mineru_timeout_seconds, remaining)

    def api(client, method, suffix, **kwargs):
        response = client.request(
            method, root + suffix, headers=headers, timeout=timeout(), **kwargs
        )
        response.raise_for_status()
        result = response.json()
        if result.get("code") != 0:
            # 上游正文可能包含文件名/地址，不直接回显或记录。
            raise ParseBlocked("OCR_FAILED", "MinerU 拒绝请求，请检查 Token、额度及接口参数")
        return result["data"]

    try:
        with httpx.Client(follow_redirects=False) as client:
            if job_file.is_file():
                batch_id = json.loads(job_file.read_text(encoding="utf-8"))["batch_id"]
            else:
                allocation = api(
                    client,
                    "POST",
                    "/file-urls/batch",
                    json={
                        "files": [{"name": "contract.pdf", "data_id": digest, "is_ocr": True}],
                        "model_version": "vlm",
                        "enable_formula": False,
                    },
                )
                batch_id = allocation["batch_id"]
                if len(allocation["file_urls"]) != 1:
                    raise ValueError("Invalid upload allocation")
                uploaded = client.put(
                    _remote_url(allocation["file_urls"][0]),
                    content=source,
                    timeout=timeout(),
                )
                uploaded.raise_for_status()
                # 只保存任务编号，不持久化 Token 或有签名的 URL。
                _write_json(job_file, {"batch_id": batch_id})
            while True:
                status = api(client, "GET", "/extract-results/batch/" + batch_id)
                rows = status["extract_result"]
                if len(rows) != 1:
                    raise ValueError("Invalid result count")
                row = rows[0]
                if row["state"] == "done":
                    content = bytearray()
                    with client.stream(
                        "GET", _remote_url(row["full_zip_url"]), timeout=timeout()
                    ) as response:
                        response.raise_for_status()
                        for chunk in response.iter_bytes():
                            timeout()
                            content.extend(chunk)
                            if len(content) > MAX_ZIP_BYTES:
                                raise ValueError("ZIP exceeds download limit")
                    result = read_result_zip(bytes(content))
                    _write_json(result_file, result)
                    return result
                if row["state"] == "failed":
                    job_file.unlink(missing_ok=True)
                    raise ParseBlocked("OCR_FAILED", "MinerU 解析失败，请检查原件或稍后重试")
                if row["state"] not in {"waiting-file", "pending", "running", "converting"}:
                    raise ValueError("Unknown task state")
                time.sleep(min(settings.mineru_poll_interval_seconds, timeout()))
    except ParseBlocked:
        raise
    except httpx.TimeoutException:
        raise ParseBlocked("OCR_TIMEOUT", "MinerU 请求超时，管理员可重试") from None
    except httpx.HTTPStatusError as exc:
        raise ParseBlocked(
            "OCR_FAILED", f"MinerU 请求返回 HTTP {exc.response.status_code}"
        ) from None
    except httpx.RequestError:
        raise ParseBlocked("OCR_FAILED", "MinerU 网络连接失败，请检查服务配置") from None
    except Exception:
        raise ParseBlocked("OCR_RESULT_INVALID", "MinerU 返回数据不符合已验证协议") from None
