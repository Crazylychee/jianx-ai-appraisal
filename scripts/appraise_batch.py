#!/usr/bin/env python3
"""多图已经对上鉴定点时用：签发、上传、批量单鉴、整鉴一次完成。

不要自己调 create_uploads，不要写上传清单，不要逐张 appraise_point。

  python scripts/appraise_batch.py --kind-id 88 --brand-id 236 --series-id 880001 --item 瓶身正面=C:/a.jpg --item 瓶身反面=C:/b.jpg
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import catalog_query as cq  # noqa: E402
import mcp_call  # noqa: E402
from appraise_multi import emit, fail, genuine_text, upload_images  # noqa: E402


def points_of(brand_id: int, series_id: int) -> List[Dict[str, Any]]:
    """系列点在 2:系列编号。没有系列时，鉴定点挂在 1:品牌编号，例如卡牌。"""
    catalog = cq.load()
    table = catalog.get("points") or {}
    if series_id > 0:
        return list(table.get("2:%d" % series_id) or [])
    return list(table.get("1:%d" % brand_id) or [])


def match_point(points: List[Dict[str, Any]], name: str) -> Dict[str, Any]:
    want = cq.norm(name)
    for point in points:
        if cq.norm(point.get("point_name")) == want:
            return point
    hits = [point for point in points if want and want in cq.norm(point.get("point_name"))]
    if len(hits) == 1:
        return hits[0]
    names = [str(point.get("point_name") or "") for point in points]
    raise SystemExit(fail("点位名没有对上", query=name, point_names=names))


def names_for(kind_id: int, brand_id: int, series_id: int) -> Dict[str, str]:
    catalog = cq.load()
    kind = cq.find_kind(catalog, None, kind_id) or {}
    brand = cq.find_brand(catalog, kind_id, None, brand_id) or {}
    series = cq.find_series(catalog, brand_id, None, series_id) or {}
    if series_id == 880001:
        series = {"series_id": 880001, "series_name": "全品牌通用瓶鉴定", "series_en_name": "全品牌通用瓶鉴定"}
    elif series_id == 880002:
        series = {"series_id": 880002, "series_name": "全品牌通用盒鉴定", "series_en_name": "全品牌通用盒鉴定"}
    return {
        "kind_name": str(kind.get("kind_name") or ""),
        "brand_name": str(brand.get("brand_name") or ""),
        "brand_en_name": str(brand.get("brand_en_name") or ""),
        "series_name": str(series.get("series_name") or ""),
        "series_en_name": str(series.get("series_en_name") or ""),
    }


def saids_of(reply: Any) -> List[str]:
    data = reply.get("data") if isinstance(reply, dict) and isinstance(reply.get("data"), dict) else reply
    if not isinstance(data, dict):
        return []
    text = str(data.get("saids") or "").strip()
    if text:
        return [part for part in text.split(",") if part.strip()]
    found = []
    for row in data.get("results") or []:
        if isinstance(row, dict) and str(row.get("said") or "").strip():
            found.append(str(row.get("said")).strip())
    return found


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="签发、上传并批量单鉴")
    parser.add_argument("--kind-id", type=int, required=True)
    parser.add_argument("--brand-id", type=int, required=True)
    parser.add_argument("--series-id", type=int, required=True)
    parser.add_argument("--item", action="append", required=True, help="点位名=图片绝对路径，可重复")
    args = parser.parse_args()
    pairs = []
    for raw in args.item:
        if "=" not in raw:
            return fail("--item 必须是 点位名=图片路径", item=raw)
        name, path = raw.split("=", 1)
        pairs.append((name.strip(), path.strip().strip('"')))
    if len(pairs) < 2:
        return fail("少于 2 张请走逐张单鉴，不要用本脚本")
    points = points_of(args.brand_id, args.series_id)
    if not points:
        if args.series_id > 0:
            return fail("这个系列没有鉴定点", series_id=args.series_id)
        return fail("这个品牌没有鉴定点", brand_id=args.brand_id)
    matched = []
    for name, path in pairs:
        point = match_point(points, name)
        matched.append((point, path))
    try:
        file_ids, uploaded = upload_images([path for _, path in matched])
    except Exception as exc:
        return fail(str(exc))
    items = []
    for (point, _), file_id in zip(matched, file_ids):
        items.append({
            "file_id": file_id,
            "point_id": int(point.get("point_id") or 0),
            "point_name": point.get("point_name") or "",
        })
    saids: List[str] = []
    results: List[Any] = []
    for start in range(0, len(items), 10):
        chunk = [{"file_id": row["file_id"], "point_id": row["point_id"]} for row in items[start:start + 10]]
        reply = mcp_call.call_tool("appraise_points", {"items": chunk})
        saids.extend(saids_of(reply))
        data = reply.get("data") if isinstance(reply, dict) and isinstance(reply.get("data"), dict) else reply
        if isinstance(data, dict) and isinstance(data.get("results"), list):
            results.extend(data.get("results") or [])
    if not saids:
        return fail("批量单鉴没有有效结果", results=results, items=items)
    names = names_for(args.kind_id, args.brand_id, args.series_id)
    overall = mcp_call.call_tool("appraise_overall", {
        "must_count": len(saids),
        "done_count": len(saids),
        "kind_id": args.kind_id,
        "brand_id": args.brand_id,
        "series_id": args.series_id,
        "category_name": names["kind_name"],
        "brand_name": names["brand_name"],
        "brand_en_name": names["brand_en_name"],
        "series_name": names["series_name"],
        "series_en_name": names["series_en_name"],
        "saids": ",".join(saids),
    })
    data = overall.get("data") if isinstance(overall, dict) and isinstance(overall.get("data"), dict) else overall
    if not isinstance(data, dict):
        data = {}
    if str(data.get("status") or "").lower() != "success":
        return fail(str(data.get("message") or "整鉴失败"), overall=data, saids=saids)
    emit({
        "ok": True,
        "kind_id": args.kind_id,
        "brand_id": args.brand_id,
        "series_id": args.series_id,
        "kind_name": names["kind_name"],
        "brand_name": names["brand_name"],
        "series_name": names["series_name"],
        "oaid": data.get("oaid") or "",
        "genuine": data.get("genuine"),
        "genuine_text": data.get("genuine_text") or genuine_text(data.get("genuine")),
        "grade": data.get("grade"),
        "description": data.get("description") or "",
        "singles": data.get("singles") or [],
        "items": items,
        "uploads": uploaded,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
