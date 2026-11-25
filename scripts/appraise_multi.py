#!/usr/bin/env python3
"""多图名单的唯一入口：认款、上传、算法多图鉴定，一次打印报告。

Agent 不要自己拼 create_uploads、不要写 /tmp、不要逐张认鉴定点。

  python scripts/appraise_multi.py --kind 包袋 --brand MCM --series PATRICIA --images 外观.jpg 五金.jpg --waiguan 外观.jpg
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import catalog_query as cq  # noqa: E402
import mcp_call  # noqa: E402
from put_appraisal_images import put_with_fallback  # noqa: E402


MULTI_KINDS = {"包袋", "鞋靴", "服饰", "美妆", "腕表", "配饰", "酒水"}
BRAND_ONLY = {"服饰", "腕表", "配饰"}
CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def emit(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def fail(error: str, **extra: Any) -> int:
    out: Dict[str, Any] = {"ok": False, "error": error}
    out.update(extra)
    emit(out)
    return 1


def pick(rec: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in rec and rec[key] not in (None, ""):
            return rec[key]
    lower = {str(k).lower(): v for k, v in rec.items()}
    for key in keys:
        val = lower.get(key.lower())
        if val not in (None, ""):
            return val
    return ""


def guess_content_type(path: str) -> str:
    return CONTENT_TYPES.get(Path(path).suffix.lower(), "image/jpeg")


def genuine_text(raw: Any) -> str:
    if raw is True:
        return "真"
    if raw is False:
        return "假"
    try:
        num = int(float(str(raw).strip()))
    except Exception:
        return "暂无法判断真假"
    if num == 1:
        return "真"
    if num == 0:
        return "假"
    return "暂无法判断真假"


def resolve_style(kind: str, brand: str, series: str, series_id: int = 0) -> Dict[str, Any]:
    catalog = cq.load()
    kind_row = cq.find_kind(catalog, kind, None)
    if not kind_row:
        return {"ok": False, "error": "品类未找到", "kind": kind}
    brand_row = cq.find_brand(catalog, int(kind_row["kind_id"]), brand, None)
    if not brand_row:
        suggestions = []
        return {"ok": False, "error": "品牌未找到", "kind_id": kind_row["kind_id"], "brand": brand, "suggestions": suggestions}
    series_name = series.strip()
    kind_name = str(kind_row.get("kind_name") or "")
    brand_info = cq.points_for(catalog, brand_row, None)
    brand_key = ""
    for point in brand_info["points"]:
        key = str(point.get("single_key") or "").strip()
        if key:
            brand_key = key
            break
    brand_level = brand_info["point_count"] > 0 and bool(brand_key)
    if series_id <= 0 and not series_name and kind_name not in BRAND_ONLY and not brand_level:
        return {
            "ok": False,
            "error": "这个品类要带上系列名",
            "kind_name": kind_name,
            "brand_name": brand_row.get("brand_name"),
            "suggestions": cq.suggest_series(catalog, kind_row, brand_row, "", 5),
        }
    owner_kind = kind_row
    owner_brand = brand_row
    series_row = None
    if series_id > 0:
        for alt_brand in cq.same_name_brands(catalog, brand_row):
            found = cq.find_series(catalog, int(alt_brand["brand_id"]), None, series_id)
            if found is not None:
                series_row = found
                owner_kind = {"kind_id": alt_brand.get("kind_id"), "kind_name": alt_brand.get("kind_name") or ""}
                owner_brand = alt_brand
                break
    else:
        series_row = cq.find_series(
            catalog,
            int(brand_row["brand_id"]),
            series_name or None,
            None,
        )
    used_brand_level = False
    if series_name and series_row is None:
        suggestions = cq.suggest_series(catalog, kind_row, brand_row, series_name, 5)
        has_whole = any(
            cq.is_whole_series(str(item.get("series_name") or ""), str(item.get("series_en_name") or ""))
            for item in suggestions
        )
        if brand_level and not has_whole:
            used_brand_level = True
            series_row = None
        else:
            return {
                "ok": False,
                "error": "系列名没有精确对上",
                "kind_id": kind_row["kind_id"],
                "kind_name": kind_name,
                "brand_id": brand_row.get("brand_id"),
                "brand_name": brand_row.get("brand_name") or "",
                "query_series": series_name,
                "suggestions": suggestions,
            }
    if series_id > 0 and series_row is None:
        return {
            "ok": False,
            "error": "series_id 不在这个品牌下",
            "series_id": series_id,
            "suggestions": cq.suggest_series(catalog, kind_row, brand_row, series_name, 5),
        }
    info = cq.points_for(catalog, owner_brand, series_row)
    single_key = ""
    for point in info["points"]:
        key = str(point.get("single_key") or "").strip()
        if key:
            single_key = key
            break
    if info["point_count"] <= 0 or not single_key:
        return {
            "ok": False,
            "error": "这一款暂时不支持 AI 鉴定",
            "kind_name": kind_row.get("kind_name"),
            "brand_name": brand_row.get("brand_name"),
            "series_name": (series_row or {}).get("series_name") or "",
            "point_count": info["point_count"],
        }
    return {
        "ok": True,
        "kind_id": owner_kind.get("kind_id"),
        "kind_name": owner_kind.get("kind_name") or "",
        "brand_id": owner_brand.get("brand_id"),
        "brand_name": owner_brand.get("brand_name") or "",
        "brand_en_name": owner_brand.get("brand_en_name") or "",
        "series_id": (series_row or {}).get("series_id") or 0,
        "series_name": (series_row or {}).get("series_name") or "",
        "series_en_name": (series_row or {}).get("series_en_name") or "",
        "level": "brand" if used_brand_level or series_row is None else "series",
        "query_series": series_name if used_brand_level else "",
        "single_key": single_key,
        "point_count": info["point_count"],
        "must_count": info["must_count"],
    }


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def extract_upload_items(payload: Any) -> List[Dict[str, Any]]:
    rows: List[Any] = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        body = _as_dict(payload.get("body")) or _as_dict(payload.get("raw_body"))
        data = payload.get("data")
        if not isinstance(data, (dict, list)):
            data = body.get("data")
        candidates = [data, body.get("data") if body else None, payload, body or None]
        for candidate in candidates:
            if isinstance(candidate, list) and candidate:
                rows = candidate
                break
            if isinstance(candidate, dict):
                inner = candidate.get("uploads") or candidate.get("items") or candidate.get("list") or []
                if isinstance(inner, list) and inner:
                    rows = inner
                    break
                if pick(candidate, "file_id", "fileId", "upload_url", "uploadUrl"):
                    rows = [candidate]
                    break
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append({
            "file_id": str(pick(row, "file_id", "fileId", "id") or "").strip(),
            "upload_url": str(pick(row, "upload_url", "uploadUrl", "url", "put_url") or "").strip(),
            "content_type": str(pick(row, "content_type", "contentType") or "").strip(),
        })
    return out


def describe_upload_failure(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "create_upload 没有返回上传地址"
    body = _as_dict(payload.get("body")) or _as_dict(payload.get("raw_body"))
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    code = payload.get("error_code", payload.get("errorCode"))
    message = str(payload.get("message") or payload.get("errorMsg") or "").strip()
    if body:
        code = body.get("error_code", body.get("errorCode", code))
        message = str(body.get("message") or body.get("errorMsg") or message).strip()
    if not message and data:
        message = str(data.get("message") or "").strip()
    status = payload.get("status_code")
    if message and code not in (0, None, "0"):
        return "签发上传地址失败: %s" % message
    if status not in (None, 200, "200"):
        return "签发上传地址失败: HTTP %s" % status
    return "create_upload 没有返回上传地址"


def upload_images(paths: Sequence[str]) -> Tuple[List[str], List[Dict[str, Any]]]:
    items = []
    abs_paths: List[str] = []
    for raw in paths:
        path = str(Path(raw).expanduser())
        if not Path(path).is_file():
            raise FileNotFoundError("找不到图片: %s" % raw)
        abs_paths.append(str(Path(path).resolve()))
        items.append({"contentType": guess_content_type(path)})
    if len(items) < 2:
        raise ValueError("多图脚本至少 2 张图")
    if len(items) > 30:
        raise ValueError("最多 30 张图")
    signed = mcp_call.call_tool("create_uploads", {"items": items})
    rows = extract_upload_items(signed)
    if len(rows) != len(abs_paths):
        rows = []
        for path in abs_paths:
            one = mcp_call.call_tool("create_upload", {"contentType": guess_content_type(path)})
            got = extract_upload_items(one)
            if not got:
                raise RuntimeError(describe_upload_failure(one))
            rows.append(got[0])
    file_ids: List[str] = []
    uploaded: List[Dict[str, Any]] = []
    for path, row in zip(abs_paths, rows):
        content_type = row.get("content_type") or guess_content_type(path)
        upload_url = row.get("upload_url") or ""
        file_id = row.get("file_id") or ""
        if not upload_url or not file_id:
            raise RuntimeError("签发结果缺 upload_url 或 file_id")
        put = put_with_fallback(upload_url, Path(path).read_bytes(), content_type)
        if not put.get("ok"):
            raise RuntimeError("上传失败 %s: %s" % (path, put.get("error") or put.get("status")))
        file_ids.append(file_id)
        uploaded.append({"file_id": file_id, "path": path, "ok": True})
    return file_ids, uploaded


def place_waiguan(images: Sequence[str], waiguan: str) -> Tuple[List[str], str]:
    ordered = [str(item).strip() for item in images if str(item).strip()]
    mark = str(waiguan or "").strip()
    if not mark:
        return ordered, "0"
    if mark not in ordered:
        raise ValueError("外观图必须是 --images 里的一张")
    rest = [item for item in ordered if item != mark]
    return [mark] + rest, "1"


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="算法多图鉴定")
    parser.add_argument("--kind", required=True)
    parser.add_argument("--brand", required=True)
    parser.add_argument("--series", default="")
    parser.add_argument("--series-id", type=int, default=0)
    parser.add_argument("--images", nargs="+", required=True)
    parser.add_argument("--waiguan", default="")
    args = parser.parse_args()
    if args.kind in ("钱币", "古钱", "古钱币", "硬币", "机制币", "瓷器", "邮票", "玉石", "翡翠", "和田玉", "其他玉石", "珠宝玉石") or args.kind.startswith("瓷器") or args.kind.startswith("邮票"):
        return fail("这个品类走 appraise_category.py", kind=args.kind, script="appraise_category.py")
    if args.kind not in MULTI_KINDS:
        return fail(
            "这个品类不走算法多图鉴定",
            kind=args.kind,
            multi_kinds=sorted(MULTI_KINDS),
        )
    if args.series_id in (880001, 880002) or "全品牌通用" in args.series:
        return fail(
            "通用瓶鉴定和通用盒鉴定不能走算法多图，请逐一单鉴",
            mode="single_point",
            kind_id=88,
            series_id=args.series_id or (880002 if "盒" in args.series else 880001),
        )
    style = resolve_style(args.kind, args.brand, args.series, args.series_id)
    if not style.get("ok"):
        emit(style)
        return 1
    try:
        ordered, has_waiguan = place_waiguan(args.images, args.waiguan)
        file_ids, uploaded = upload_images(ordered)
    except Exception as exc:
        return fail(str(exc), style=style)
    report = mcp_call.call_tool("appraise_nopointer", {
        "file_ids": file_ids,
        "has_waiguan": has_waiguan,
        "kind_id": style["kind_id"],
        "brand_id": style["brand_id"],
        "series_id": style.get("series_id") or 0,
        "single_key": style["single_key"],
        "brand_name": style.get("brand_name") or "",
        "series_name": style.get("series_name") or "",
    })
    data = report.get("data") if isinstance(report, dict) and isinstance(report.get("data"), dict) else report
    if not isinstance(data, dict):
        data = {"raw": data}
    status = str(data.get("status") or "").strip().lower()
    oaid = str(data.get("oaid") or data.get("oa_id") or "").strip()
    if status == "success" and oaid:
        emit({
            "ok": True,
            "kind_name": style.get("kind_name"),
            "brand_name": style.get("brand_name"),
            "series_name": style.get("series_name"),
            "oaid": oaid,
            "genuine": data.get("genuine"),
            "genuine_text": data.get("genuine_text") or genuine_text(data.get("genuine")),
            "grade": data.get("grade_100") if data.get("grade_100") is not None else data.get("grade"),
            "description": data.get("description") or "",
            "points_description": data.get("points_description") or "",
            "single_summary": data.get("single_summary") or "",
            "singles": data.get("singles") or [],
            "file_ids": file_ids,
            "uploads": uploaded,
        })
        return 0
    return fail(
        str(data.get("message") or data.get("error") or "算法多图鉴定失败"),
        style=style,
        file_ids=file_ids,
        reply=data,
    )


if __name__ == "__main__":
    raise SystemExit(main())
