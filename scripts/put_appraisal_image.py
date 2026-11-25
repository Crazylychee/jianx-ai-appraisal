#!/usr/bin/env python3
"""把本地图片 PUT 到 create_upload 返回的 upload_url。

WorkBuddy 本地代理常会给外发请求塞 Authorization / OSS STS 头。
阿里云 OSS 的 URL 签名（查询参数 Signature）一旦再带 Authorization，就会 400。
本脚本先按签名 URL 干净上传（不带 Authorization）；失败后再去掉查询参数重试。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from typing import Dict, Tuple
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
    """WorkBuddy 代理常给外发 PUT 塞 Authorization，签名 URL 会 400。

    默认先打无查询参数地址（proxy_sts）；失败再试带签名的原 URL。
    环境变量 JIANX_PUT_MODE=signed 可改回「先签名后回退」。
    """
    import os

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


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass
    if len(sys.argv) != 4:
        print(
            "usage: put_appraisal_image.py <upload_url> <local_path> <content_type>",
            file=sys.stderr,
        )
        return 2
    upload_url, local_path, content_type = sys.argv[1], sys.argv[2], sys.argv[3]
    content_type = content_type.strip()
    if not content_type:
        emit({"ok": False, "error": "content_type 为空"})
        return 2
    try:
        with open(local_path, "rb") as fh:
            body = fh.read()
    except OSError as exc:
        emit({"ok": False, "error": str(exc)})
        return 1
    if not body:
        emit({"ok": False, "error": "本地文件为空"})
        return 2
    out = put_with_fallback(upload_url, body, content_type)
    emit(out)
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
