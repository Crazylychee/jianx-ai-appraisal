#!/usr/bin/env python3
"""从鉴X正式环境导出目录，写入 data/catalog.json（供 catalog_query.py 读取，勿直接给 Agent 读）。

用法：
  python export_catalog.py                 # 导出全部品类（增量落盘）
  python export_catalog.py --fresh         # 清空后全量
  python export_catalog.py --kind-id 84
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "catalog.json"
DEFAULT_HOST = "https://jianx-shechao.turingsenseai.com"
# 无真实鉴定目录的占位品类，跳过品牌展开
SKIP_KIND_IDS = {99999}


def post(host: str, path: str, body: Dict[str, Any], timeout: int = 45) -> Any:
    req = urllib.request.Request(
        host.rstrip("/") + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError("%s HTTP %s" % (path, exc.code)) from exc
    if int(payload.get("error_code") or 0) != 0:
        raise RuntimeError("%s -> %s" % (path, payload.get("message") or payload))
    return payload.get("data")


def load_existing() -> Dict[str, Any]:
    if OUT.exists():
        return json.loads(OUT.read_text(encoding="utf-8"))
    legacy = ROOT / "references" / "catalog.json"
    if legacy.exists():
        return json.loads(legacy.read_text(encoding="utf-8"))
    return {"kinds": [], "brands": {}, "series": {}, "points": {}}


def save(catalog: Dict[str, Any]) -> None:
    catalog["covered_kind_ids"] = sorted(
        (catalog.get("brands") or {}).keys(),
        key=lambda x: int(x) if str(x).isdigit() else 0,
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUT)


def slim_point(p: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "point_id": int(p.get("point_id") or 0),
        "point_name": str(p.get("point_name") or ""),
        "must": int(p.get("must") or 0),
        "single_key": str(p.get("single_key") or ""),
        "point_key": str(p.get("point_key") or p.get("key") or ""),
    }


def fetch_points(host: str, query_id: int, hierarchy: str) -> List[Dict[str, Any]]:
    data = post(host, "/api/v1/basic/pointListV2", {
        "query_id": query_id,
        "hierarchy": hierarchy,
        "type": 1,
        "is_must_adv": 1,
    })
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("result") or data.get("points") or []
    else:
        rows = []
    return [slim_point(p) for p in rows if isinstance(p, dict) and int(p.get("point_id") or 0) > 0]


def export_brand(host: str, bid: int, related: bool) -> Tuple[int, List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    series = post(host, "/api/v1/basic_pure/seriesList", {"brand_id": bid}) or []
    series_rows: List[Dict[str, Any]] = []
    point_jobs: List[Tuple[str, int, str]] = []
    for s in series:
        if not isinstance(s, dict):
            continue
        sid = int(s.get("id") or s.get("series_id") or 0)
        if sid <= 0:
            continue
        s_related = bool(s.get("is_related"))
        series_rows.append({
            "series_id": sid,
            "series_name": str(s.get("name") or s.get("series_name") or ""),
            "series_en_name": str(s.get("en_name") or s.get("series_en_name") or ""),
            "is_related": s_related,
        })
        if s_related:
            point_jobs.append(("2:%d" % sid, sid, "2"))
    if related:
        point_jobs.append(("1:%d" % bid, bid, "1"))

    points: Dict[str, List[Dict[str, Any]]] = {}
    if point_jobs:
        with ThreadPoolExecutor(max_workers=min(12, len(point_jobs))) as pool:
            futs = {
                pool.submit(fetch_points, host, qid, hier): key
                for key, qid, hier in point_jobs
            }
            for fut in as_completed(futs):
                key = futs[fut]
                try:
                    points[key] = fut.result()
                except Exception as exc:
                    print("warn points", key, exc, flush=True)
                    points[key] = []
    return bid, series_rows, points


def export_kind(
    host: str,
    catalog: Dict[str, Any],
    kind_id: int,
    brand_filter: set,
    brand_workers: int,
) -> None:
    brands = post(host, "/api/v1/basic_pure/brandList", {"kind_id": kind_id}) or []
    brand_rows: List[Dict[str, Any]] = []
    jobs = []
    for b in brands:
        if not isinstance(b, dict):
            continue
        bid = int(b.get("id") or b.get("brand_id") or 0)
        if bid <= 0:
            continue
        if brand_filter and bid not in brand_filter:
            continue
        related = bool(b.get("is_related"))
        brand_rows.append({
            "brand_id": bid,
            "brand_name": str(b.get("name") or b.get("brand_name") or ""),
            "brand_en_name": str(b.get("en_name") or b.get("brand_en_name") or ""),
            "is_related": related,
        })
        jobs.append((bid, related))

    if brand_filter and str(kind_id) in catalog["brands"]:
        existing = {int(x.get("brand_id") or 0): x for x in catalog["brands"][str(kind_id)]}
        for row in brand_rows:
            existing[row["brand_id"]] = row
        catalog["brands"][str(kind_id)] = list(existing.values())
    else:
        catalog["brands"][str(kind_id)] = brand_rows

    done = 0
    total = len(jobs)
    with ThreadPoolExecutor(max_workers=min(brand_workers, max(1, total))) as pool:
        futs = [pool.submit(export_brand, host, bid, related) for bid, related in jobs]
        for fut in as_completed(futs):
            done += 1
            try:
                bid, series_rows, points = fut.result()
            except Exception as exc:
                print("warn brand kind=%s %s" % (kind_id, exc), flush=True)
                continue
            catalog["series"][str(bid)] = series_rows
            catalog["points"].update(points)
            if done % 50 == 0 or done == total:
                print("kind", kind_id, "progress", done, "/", total, flush=True)
    print("kind", kind_id, "brands", len(brand_rows), "done", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--kind-id", type=int, action="append", default=[])
    parser.add_argument("--brand-id", type=int, action="append", default=[])
    parser.add_argument("--fresh", action="store_true", help="丢弃旧 brands/series/points 全量重导")
    parser.add_argument("--brand-workers", type=int, default=16)
    parser.add_argument("--resume", action="store_true", help="跳过已有 brands 的品类")
    args = parser.parse_args()

    catalog = {"kinds": [], "brands": {}, "series": {}, "points": {}} if args.fresh else load_existing()
    kinds = post(args.host, "/api/v1/basic_pure/kindList", {}) or []
    catalog["kinds"] = [
        {"kind_id": int(k.get("kind_id") or 0), "kind_name": str(k.get("kind_name") or "")}
        for k in kinds
        if int(k.get("kind_id") or 0) > 0
    ]
    catalog.setdefault("brands", {})
    catalog.setdefault("series", {})
    catalog.setdefault("points", {})
    save(catalog)

    brand_filter = set(args.brand_id or [])
    kind_ids = list(args.kind_id or [])
    if not kind_ids:
        kind_ids = [int(k["kind_id"]) for k in catalog["kinds"] if int(k["kind_id"]) not in SKIP_KIND_IDS]

    for kind_id in kind_ids:
        if args.resume and str(kind_id) in (catalog.get("brands") or {}) and (catalog["brands"][str(kind_id)]):
            print("skip covered kind", kind_id, flush=True)
            continue
        try:
            export_kind(args.host, catalog, kind_id, brand_filter, max(4, args.brand_workers))
            save(catalog)
        except Exception as exc:
            print("warn kind", kind_id, exc, flush=True)
            save(catalog)

    for legacy in (
        ROOT / "references" / "catalog.json",
        ROOT / "references" / "catalog_index.json",
    ):
        if legacy.exists():
            legacy.unlink()
            try:
                legacy.parent.rmdir()
            except OSError:
                pass

    print("wrote", OUT, "bytes", OUT.stat().st_size, flush=True)
    print(
        "kinds", len(catalog["kinds"]),
        "covered", len(catalog.get("covered_kind_ids") or []),
        "brand_groups", len(catalog.get("brands") or {}),
        "series_groups", len(catalog.get("series") or {}),
        "point_groups", len(catalog.get("points") or {}),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
