#!/usr/bin/env python3
"""签发、上传、识别款式，一次打印结果。

本地路径会先签发再上传，换成 https 后调用 recognize_style。
已经是 https 的地址直接使用，不再上传。

  python scripts/recognize_style.py --images C:/a.jpg
  python scripts/recognize_style.py --images C:/外观.jpg C:/五金.jpg --waiguan C:/外观.jpg
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import mcp_call  # noqa: E402
from appraise_category import resolve_image, upload_one  # noqa: E402
from appraise_multi import emit, fail  # noqa: E402


KIND_IDS = "83,84,85,88,90,92"
SUANFA_KIND_IDS = "83,84,88,90,92"
MAX_IMAGES = 9


def http_url(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("https://") or text.startswith("http://"):
        return text
    return ""


def same_image(left: str, right: str) -> bool:
    if http_url(left) and http_url(right):
        return http_url(left) == http_url(right)
    try:
        return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()
    except OSError:
        return str(left).strip() == str(right).strip()


def order_images(images: Sequence[str], waiguan: str) -> List[str]:
    ordered = [str(item).strip() for item in images if str(item).strip()]
    if not ordered:
        raise ValueError("至少一张图")
    if len(ordered) > MAX_IMAGES:
        raise ValueError("最多 %d 张图" % MAX_IMAGES)
    mark = str(waiguan or "").strip()
    if not mark:
        return ordered
    matched = [item for item in ordered if same_image(item, mark)]
    if not matched:
        raise ValueError("外观图必须是 --images 里的一张")
    rest = [item for item in ordered if not same_image(item, mark)]
    return [matched[0]] + rest


def to_public_url(item: str) -> str:
    ready = http_url(item)
    if ready:
        return ready
    path = Path(item).expanduser()
    if not path.is_file():
        raise FileNotFoundError("找不到图片: %s" % item)
    file_id = upload_one(str(path))
    return resolve_image(file_id)


def nint(value: Any) -> int:
    try:
        return int(float(str(value).strip() or 0))
    except Exception:
        return 0


def as_data(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def present(reply: Any, urls: Sequence[str]) -> Dict[str, Any]:
    sent = list(urls)
    if not isinstance(reply, dict):
        return {"ok": False, "error": "工具没有返回结果", "image_urls": sent, "raw": reply}
    data = as_data(reply.get("data"))
    code = reply.get("error_code", reply.get("errorCode"))
    ok = str(code).strip() in ("0", "0.0")
    out: Dict[str, Any] = {
        "ok": ok,
        "error_code": nint(code) if code not in (None, "") else code,
        "message": str(reply.get("message") or ""),
        "kind": str(data.get("kind") or ""),
        "kind_id": nint(data.get("kind_id")),
        "brand": str(data.get("brand") or data.get("brand_name") or ""),
        "brand_en": str(data.get("brand_en") or data.get("brand_en_name") or ""),
        "brand_id": nint(data.get("brand_id")),
        "series": str(data.get("series_name") or data.get("series") or ""),
        "series_en": str(data.get("series_en_name") or data.get("series_en") or ""),
        "series_id": nint(data.get("series_id")),
        "image_url": str(data.get("image_url") or (sent[0] if sent else "")),
        "image_urls": sent,
        "data": data,
    }
    if reply.get("selected_index") not in (None, ""):
        out["selected_index"] = nint(reply.get("selected_index"))
    if not ok and not out["message"]:
        out["error"] = str(reply.get("error") or "识款失败")
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="签发、上传并识别款式")
    parser.add_argument("--images", nargs="+", required=True)
    parser.add_argument("--waiguan", default="")
    args = parser.parse_args()
    try:
        ordered = order_images(args.images, args.waiguan)
        urls = [to_public_url(item) for item in ordered]
    except Exception as exc:
        return fail(str(exc))
    payload: Dict[str, Any] = {
        "image_url": urls[0],
        "kind_ids": KIND_IDS,
        "suanfa_kind_ids": SUANFA_KIND_IDS,
    }
    if len(urls) > 1:
        payload["urls"] = urls
    reply = mcp_call.call_tool("recognize_style", payload)
    result = present(reply, urls)
    emit(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
