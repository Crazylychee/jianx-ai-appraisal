#!/usr/bin/env python3
"""钱币、邮票、瓷器、玉石。上传后仍调用 appraise_point / appraise_overall。

route=jianbao 或 route=yujian。不查目录，不对鉴定点。

  python scripts/appraise_category.py point --kind 瓷器 --side 正面 --image C:/a.jpg
  python scripts/appraise_category.py point --kind 瓷器 --side 反面 --image C:/b.jpg
  python scripts/appraise_category.py overall --kind 瓷器 --ront-said SAID1 --obverse-said SAID2 --oaid OAID
  python scripts/appraise_category.py point --kind 翡翠 --image C:/a.jpg
  python scripts/appraise_category.py overall --kind 翡翠 --said1 SAID1 --said2 SAID2 --said3 SAID3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import mcp_call  # noqa: E402
from appraise_multi import (  # noqa: E402
    describe_upload_failure,
    emit,
    extract_upload_items,
    fail,
    guess_content_type,
)
from put_appraisal_images import put_with_fallback  # noqa: E402


JIANX_TEST = "https://jianx-shechao.turingsenseai.com"
JIANBAO_TEST = "https://turing-jianbao-server.turingsenseai.com"
YU = {
    "玉石": ("0", "FC", "翡翠"),
    "翡翠": ("0", "FC", "翡翠"),
    "珠宝玉石": ("0", "FC", "翡翠"),
    "和田玉": ("0.2", "HTY", "和田玉"),
    "其他玉石": ("0.1", "QTYS", "其他玉石"),
}


def is_yujian(kind: str) -> bool:
    return kind.strip() in YU


def single_kind(kind: str, side: str) -> str:
    name = kind.strip()
    face = side.strip()
    if name.startswith("瓷器"):
        if name == "瓷器反面" or face == "反面":
            return "瓷器反面"
        return "瓷器正面"
    if name.startswith("邮票"):
        if name == "邮票反面" or face == "反面":
            return "邮票反面"
        return "邮票正面"
    if name in ("钱币", "古钱", "古钱币", "花钱", "厌胜钱"):
        return "古钱"
    if name in ("硬币", "机制币"):
        return name
    raise ValueError("这个品类不走鉴宝或鉴玉")


FULL_POINTS = {
    "古钱": ("正面", "反面"),
    "硬币": ("正面", "反面"),
    "机制币": ("正面", "反面"),
    "瓷器": ("外观", "底足"),
}


def missing_full_points(mapped: str, front: str, back: str) -> str:
    pair = FULL_POINTS.get(mapped)
    if not pair:
        return ""
    present = [pair[0] if str(front or "").strip() else "", pair[1] if str(back or "").strip() else ""]
    lack = [name for name, ok in zip(pair, present) if not ok]
    if not lack:
        return ""
    label = "钱币" if mapped in ("古钱", "硬币", "机制币") else mapped
    return "%s要%s都单鉴成功后才能整鉴，还缺%s" % (label, "和".join(pair), "、".join(lack))


def overall_kind(kind: str) -> str:
    name = kind.strip()
    if name.startswith("瓷器"):
        return "瓷器"
    if name.startswith("邮票"):
        return "邮票"
    if name in ("钱币", "古钱", "古钱币", "花钱", "厌胜钱"):
        return "古钱"
    if name in ("硬币", "机制币"):
        return name
    raise ValueError("这个品类不走鉴宝或鉴玉")


def upload_one(path: str) -> str:
    image = Path(path).expanduser()
    if not image.is_file():
        raise FileNotFoundError("找不到图片: %s" % path)
    content_type = guess_content_type(str(image))
    signed: Any = {}
    rows: List[Dict[str, Any]] = []
    for attempt in range(3):
        signed = mcp_call.call_tool("create_upload", {"contentType": content_type})
        rows = extract_upload_items(signed)
        if rows and rows[0].get("upload_url") and rows[0].get("file_id"):
            break
        if attempt < 2:
            time.sleep(0.4)
    if not rows:
        raise RuntimeError(describe_upload_failure(signed))
    row = rows[0]
    upload_url = row.get("upload_url") or ""
    file_id = row.get("file_id") or ""
    if not upload_url or not file_id:
        raise RuntimeError("签发结果缺 upload_url 或 file_id")
    put = put_with_fallback(upload_url, image.read_bytes(), row.get("content_type") or content_type)
    if not put.get("ok"):
        raise RuntimeError("上传失败: %s" % (put.get("error") or put.get("status")))
    return file_id


def resolve_image(file_id: str) -> str:
    req = urllib.request.Request(
        JIANX_TEST + "/api/v1/basic_pure/resolveUpload",
        data=json.dumps({"file_id": file_id}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        data = {}
    url = str(data.get("image_url") or data.get("imageUrl") or "").strip()
    if not url:
        raise RuntimeError("没有换成图片地址")
    return url


def post_json(url: str, body: Dict[str, Any]) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "turingResponseType": "json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def payload_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return data if isinstance(data, dict) else {}


def enrich_coin(kind: str, name: str, dynasty: str, texture: str, find: str) -> Tuple[str, str, str, str]:
    """古钱、机制币的版别来自行情库。硬币的名称和版别都是看图得到的版别。"""
    if kind == "硬币":
        edition = find.strip() or name.strip()
        return edition, dynasty, texture, edition
    if kind not in ("古钱", "机制币") or not name.strip():
        return name, dynasty, texture, find
    try:
        payload = post_json(JIANBAO_TEST + "/api/v1/flow/getCrawlerData", {
            "kind": kind,
            "name": name.strip(),
            "dynasty": dynasty,
            "texture": texture,
        })
    except Exception:
        return name, dynasty, texture, find
    data = payload_data(payload)
    code = data.get("errorCode", data.get("error_code"))
    if code not in (0, None, "0"):
        return name, dynasty, texture, find
    crawled_dynasty = str(data.get("dynasty") or "").strip()
    if crawled_dynasty in ("", "-"):
        crawled_dynasty = dynasty
    crawled_texture = str(data.get("texture") or "").strip() or texture
    crawled_find = str(data.get("find") or "").strip() or find
    return name.strip(), crawled_dynasty, crawled_texture, crawled_find


def call_and_print(name: str, arguments: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> int:
    reply = mcp_call.call_tool(name, arguments)
    if not isinstance(reply, dict):
        emit({"ok": False, "error": "工具没有返回结果", "raw": reply})
        return 1
    status = str(reply.get("status") or "").strip().lower()
    reply["ok"] = status == "success"
    for key, value in (extra or {}).items():
        if value and not reply.get(key):
            reply[key] = value
    emit(reply)
    return 0 if reply["ok"] else 1


def run_point(args: argparse.Namespace) -> int:
    kind = args.kind.strip()
    try:
        file_id = upload_one(args.image)
        image_url = resolve_image(file_id)
    except Exception as exc:
        return fail(str(exc), kind=kind)
    if is_yujian(kind):
        grade, _ident, _label = YU[kind]
        return call_and_print("appraise_point", {
            "route": "yujian",
            "image_url": image_url,
            "grade": grade,
            "kind": kind,
        }, {"image_url": image_url})
    try:
        mapped = single_kind(kind, args.side)
    except ValueError as exc:
        return fail(str(exc), kind=kind)
    return call_and_print("appraise_point", {
        "route": "jianbao",
        "kind": mapped,
        "image_url": image_url,
    }, {"image_url": image_url})


def run_overall(args: argparse.Namespace) -> int:
    kind = args.kind.strip()
    if is_yujian(kind):
        _grade, ident, label = YU[kind]
        said1 = args.said1 or args.ront_said
        if not said1:
            return fail("鉴玉整鉴需要 said1", kind=kind)
        payload = {
            "route": "yujian",
            "identification_kind": args.identification_kind or ident,
            "kind": label,
            "said1": said1,
            "said2": args.said2 or args.obverse_said,
            "said3": args.said3,
            "name": args.name,
            "conversation_id": args.conversation_id,
        }
        images = [str(item).strip() for item in (args.image_urls or []) if str(item).strip()]
        first = str(args.first_image_url or "").strip()
        if first and first not in images:
            images.insert(0, first)
        for key, url in zip(("image_url", "image_url_2", "image_url_3"), images[:3]):
            payload[key] = url
        return call_and_print("appraise_overall", payload)
    try:
        mapped = overall_kind(kind)
    except ValueError as exc:
        return fail(str(exc), kind=kind)
    blocked = missing_full_points(mapped, args.ront_said, args.obverse_said)
    if blocked:
        return fail(blocked, kind=kind)
    if not args.ront_said:
        return fail("鉴宝整鉴需要正面 said", kind=kind)
    name, dynasty, texture, find = enrich_coin(
        mapped, args.name, args.dynasty, args.texture, args.find,
    )
    return call_and_print("appraise_overall", {
        "route": "jianbao",
        "kind": mapped,
        "ront_said": args.ront_said,
        "obverse_said": args.obverse_said,
        "oaid": args.oaid,
        "name": name,
        "dynasty": dynasty,
        "texture": texture,
        "find": find,
        "first_image_url": args.first_image_url,
        "conversation_id": args.conversation_id,
    })


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="钱币邮票瓷器玉石鉴定")
    sub = parser.add_subparsers(dest="cmd", required=True)

    point = sub.add_parser("point")
    point.add_argument("--kind", required=True)
    point.add_argument("--side", default="正面")
    point.add_argument("--image", required=True)

    overall = sub.add_parser("overall")
    overall.add_argument("--kind", required=True)
    overall.add_argument("--ront-said", default="")
    overall.add_argument("--obverse-said", default="")
    overall.add_argument("--oaid", default="")
    overall.add_argument("--said1", default="")
    overall.add_argument("--said2", default="")
    overall.add_argument("--said3", default="")
    overall.add_argument("--identification-kind", default="")
    overall.add_argument("--name", default="")
    overall.add_argument("--dynasty", default="")
    overall.add_argument("--texture", default="")
    overall.add_argument("--find", default="")
    overall.add_argument("--first-image-url", default="")
    overall.add_argument("--image", action="append", default=[], dest="image_urls")
    overall.add_argument("--conversation-id", default="")

    args = parser.parse_args()
    if args.cmd == "point":
        return run_point(args)
    return run_overall(args)


if __name__ == "__main__":
    sys.exit(main())
