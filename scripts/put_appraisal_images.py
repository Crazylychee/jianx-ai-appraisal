#!/usr/bin/env python3
"""并发把多张本地图 PUT 到 create_uploads 返回的地址。

清单 JSON 示例：
[
  {"upload_url":"https://...","path":"C:/a.jpg","content_type":"image/jpeg","file_id":"mcp-appraisal/..."},
  ...
]

stdout：{"ok":true,"results":[...]}
默认并发 2（环境变量 JIANX_PUT_WORKERS 可调）。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple
from urllib.parse import urlsplit, urlunsplit


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _put(url: str, data: bytes, headers: Dict[str, str]) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method="PUT")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() or b""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 599, str(exc).encode("utf-8", errors="replace")


def _strip_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def put_with_fallback(upload_url: str, body: bytes, content_type: str) -> dict:
    headers = {"Content-Type": content_type}
    mode_pref = (os.environ.get("JIANX_PUT_MODE") or "proxy").strip().lower()
    signed_url = upload_url
    proxy_url = _strip_query(upload_url)
    if mode_pref in ("signed", "signed_first", "sign"):
        first_url, first_mode = signed_url, "signed_url"
        second_url, second_mode = proxy_url, "proxy_sts"
    else:
        first_url, first_mode = proxy_url, "proxy_sts"
        second_url, second_mode = signed_url, "signed_url"
    code, raw = _put(first_url, body, headers)
    mode = first_mode
    if code >= 400:
        time.sleep(0.15)
        code2, raw2 = _put(second_url, body, headers)
        if code2 < 400 or code2 < code:
            code, raw, mode = code2, raw2, second_mode
    ok = 200 <= code < 300
    out = {
        "ok": ok,
        "status": code,
        "mode": mode,
        "bytes": len(body),
        "content_type": content_type,
    }
    if not ok:
        out["error"] = raw.decode("utf-8", errors="replace")[:500] or ("HTTP %s" % code)
    return out


def _one(item: Dict[str, Any]) -> Dict[str, Any]:
    upload_url = str(item.get("upload_url") or "").strip()
    local_path = str(item.get("path") or item.get("local_path") or "").strip()
    content_type = str(item.get("content_type") or "").strip()
    file_id = str(item.get("file_id") or "").strip()
    out: Dict[str, Any] = {
        "ok": False,
        "file_id": file_id,
        "path": local_path,
        "content_type": content_type,
    }
    if not upload_url or not local_path or not content_type:
        out["error"] = "upload_url/path/content_type 必填"
        return out
    try:
        with open(local_path, "rb") as fh:
            body = fh.read()
    except OSError as exc:
        out["error"] = str(exc)
        return out
    if not body:
        out["error"] = "本地文件为空"
        return out
    try:
        result = put_with_fallback(upload_url, body, content_type)
    except Exception as exc:
        out["error"] = str(exc)
        return out
    out.update(result)
    out["file_id"] = file_id
    out["path"] = local_path
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass
    if len(sys.argv) != 2:
        print("usage: put_appraisal_images.py <manifest.json>", file=sys.stderr)
        return 2
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    if not isinstance(manifest, list) or not manifest:
        emit({"ok": False, "error": "清单必须是非空数组"})
        return 2
    try:
        workers = int(os.environ.get("JIANX_PUT_WORKERS") or "2")
    except Exception:
        workers = 2
    workers = max(1, min(workers, len(manifest), 4))
    results: List[Dict[str, Any]] = [None] * len(manifest)  # type: ignore
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one, item): idx for idx, item in enumerate(manifest)}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results[idx] = fut.result()
            except Exception as exc:
                results[idx] = {"ok": False, "error": str(exc)}
            time.sleep(0.05)
    ok = all(r and r.get("ok") for r in results)
    emit({"ok": ok, "results": results, "workers": workers})
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
