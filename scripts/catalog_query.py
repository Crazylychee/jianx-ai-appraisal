#!/usr/bin/env python3
"""本地目录查询（Agent 只调本脚本，禁止直接打开 data/catalog.json）。

用法示例：
  python catalog_query.py kinds
  python catalog_query.py kinds --q 包袋
  python catalog_query.py brands --kind 包袋
  python catalog_query.py brands --kind-id 83 --q 汤丽
  python catalog_query.py series --brand-id 665
  python catalog_query.py series --brand 汤丽柏琦 --q Eleanor
  python catalog_query.py points --brand-id 665 --series-id 20433
  python catalog_query.py resolve --kind 包袋 --brand 汤丽 --series Eleanor
  python catalog_query.py must --brand-id 665 --series-id 20433
"""

from __future__ import annotations

import argparse
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "catalog.json"
_CACHE: Optional[Dict[str, Any]] = None


def emit(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def load() -> Dict[str, Any]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if not CATALOG_PATH.exists():
        emit({"ok": False, "error": "缺少 data/catalog.json，请先跑 scripts/export_catalog.py"})
        sys.exit(2)
    _CACHE = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return _CACHE


def norm(s: str) -> str:
    return "".join(str(s or "").strip().lower().split())


def name_score(query: str, *fields: Any) -> float:
    folded = norm(query)
    if not folded:
        return 0.0
    best = 0.0
    for field in fields:
        other = norm(field)
        if not other:
            continue
        if folded == other:
            return 1.0
        if min(len(folded), len(other)) < 2:
            continue
        if folded in other or other in folded:
            cover = min(len(folded), len(other)) / max(len(folded), len(other))
            if other.startswith(folded) or folded.startswith(other):
                cover = max(cover, 0.86)
            best = max(best, cover)
            continue
        best = max(best, SequenceMatcher(None, folded, other).ratio())
    return best


def pick_similar(query: str, rows: Sequence[Dict[str, Any]], *keys: str) -> Optional[Dict[str, Any]]:
    """子串对不上时，用相似度选一个明显更近的名字。分不开就不要猜。"""
    scored: List[tuple] = []
    for row in rows:
        score = name_score(query, *[row.get(key) for key in keys])
        if score >= 0.72:
            scored.append((score, row))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], str(item[1].get(keys[0]) or "")))
    if len(scored) > 1 and scored[0][0] < 0.98 and scored[0][0] - scored[1][0] < 0.08:
        return None
    return scored[0][1]


def text_hit(q: str, *fields: Any) -> bool:
    nq = norm(q)
    if not nq:
        return True
    blob = norm(" ".join(str(x or "") for x in fields))
    return nq in blob


def find_kind(catalog: Dict[str, Any], kind: Optional[str], kind_id: Optional[int]) -> Optional[Dict[str, Any]]:
    kinds = catalog.get("kinds") or []
    if kind_id is not None:
        for k in kinds:
            if int(k.get("kind_id") or 0) == int(kind_id):
                return k
        return None
    if not kind:
        return None
    hits = [k for k in kinds if text_hit(kind, k.get("kind_name"), k.get("kind_id"))]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        return None
    # 精确名优先
    for k in hits:
        if norm(k.get("kind_name")) == norm(kind):
            return k
    return hits[0]


def find_brand(
    catalog: Dict[str, Any],
    kind_id: Optional[int],
    brand: Optional[str],
    brand_id: Optional[int],
) -> Optional[Dict[str, Any]]:
    if brand_id is not None:
        for kid, rows in (catalog.get("brands") or {}).items():
            if kind_id is not None and str(kid) != str(kind_id):
                continue
            for b in rows or []:
                if int(b.get("brand_id") or 0) == int(brand_id):
                    out = dict(b)
                    out["kind_id"] = int(kid)
                    return out
        return None
    if not brand:
        return None
    candidates: List[Dict[str, Any]] = []
    for kid, rows in (catalog.get("brands") or {}).items():
        if kind_id is not None and str(kid) != str(kind_id):
            continue
        for b in rows or []:
            if text_hit(brand, b.get("brand_name"), b.get("brand_en_name"), b.get("brand_id")):
                out = dict(b)
                out["kind_id"] = int(kid)
                candidates.append(out)
    if not candidates:
        pool: List[Dict[str, Any]] = []
        for kid, rows in (catalog.get("brands") or {}).items():
            if kind_id is not None and str(kid) != str(kind_id):
                continue
            for b in rows or []:
                out = dict(b)
                out["kind_id"] = int(kid)
                pool.append(out)
        picked = pick_similar(brand, pool, "brand_name", "brand_en_name")
        return dict(picked) if picked else None
    if len(candidates) == 1:
        return candidates[0]
    for b in candidates:
        if norm(b.get("brand_name")) == norm(brand) or norm(b.get("brand_en_name")) == norm(brand):
            return b
    return candidates[0]


def _makeup_fallback_series(row: Dict[str, Any]) -> bool:
    blob = str(row.get("series_name") or "") + str(row.get("series_en_name") or "")
    return "全品牌通用" in blob


def find_series(
    catalog: Dict[str, Any],
    brand_id: int,
    series: Optional[str],
    series_id: Optional[int],
) -> Optional[Dict[str, Any]]:
    rows = catalog.get("series", {}).get(str(brand_id)) or []
    if series_id is not None:
        for s in rows:
            if int(s.get("series_id") or 0) == int(series_id):
                return s
        return None
    if not series:
        # 不带系列名时不要默认成全品牌通用瓶/盒。有全系列用全系列；只有一个产品系列就用它；多个产品系列交给调用方选择。
        related = [s for s in rows if s.get("is_related")]
        for s in related:
            name = str(s.get("series_name") or "")
            if "全系列" in name or norm(s.get("series_en_name")) in ("allseries", "all"):
                return s
        product = [s for s in related if not _makeup_fallback_series(s)]
        if len(product) == 1:
            return product[0]
        if len(product) > 1:
            return None
        return related[0] if related else (rows[0] if rows else None)
    hits = [s for s in rows if text_hit(series, s.get("series_name"), s.get("series_en_name"), s.get("series_id"))]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        return pick_similar(series, rows, "series_name", "series_en_name")
    for s in hits:
        if norm(s.get("series_name")) == norm(series) or norm(s.get("series_en_name")) == norm(series):
            return s
    return hits[0]


def _grams(text: str) -> set:
    chars = [ch for ch in norm(text) if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"]
    if len(chars) < 2:
        return set(chars)
    return {"".join(chars[i:i + 2]) for i in range(len(chars) - 1)}


def same_name_brands(catalog: Dict[str, Any], brand: Dict[str, Any]) -> List[Dict[str, Any]]:
    """同一品牌名在各品类下的记录。美妆和美妆无损的 brand_id 不同，推荐时放在一起。"""
    name = norm(brand.get("brand_name"))
    en = norm(brand.get("brand_en_name"))
    kinds = {str(k.get("kind_id")): k for k in (catalog.get("kinds") or [])}
    found: List[Dict[str, Any]] = []
    for kid, rows in (catalog.get("brands") or {}).items():
        kind = kinds.get(str(kid)) or {"kind_id": int(kid), "kind_name": ""}
        for row in rows or []:
            if norm(row.get("brand_name")) == name or (en and norm(row.get("brand_en_name")) == en):
                item = dict(row)
                item["kind_id"] = int(kind.get("kind_id") or kid)
                item["kind_name"] = kind.get("kind_name") or ""
                found.append(item)
    return found


def is_whole_series(name: str, en: str) -> bool:
    """品牌底下的全系列，含「全系列（皮表带）」这类名字。"""
    text = str(name or "").strip()
    latin = str(en or "").strip().lower()
    return "全系列" in text or text.upper() == "FULL SERIES" or latin in ("allseries", "all", "full series")


def suggest_series(
    catalog: Dict[str, Any],
    kind: Dict[str, Any],
    brand: Dict[str, Any],
    query: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    qset = _grams(query)
    ranked = []
    whole: List[Dict[str, Any]] = []
    own_id = int(brand.get("brand_id") or 0)
    brands = same_name_brands(catalog, brand) or [dict(brand, kind_id=kind.get("kind_id"), kind_name=kind.get("kind_name") or "")]
    for brand_row in brands:
        rows = list(catalog.get("series", {}).get(str(brand_row.get("brand_id"))) or [])
        for row in rows:
            name = str(row.get("series_name") or "")
            en = str(row.get("series_en_name") or "")
            blob = name + en
            if "全品牌通用" in blob:
                continue
            info = points_for(catalog, brand_row, row)
            single_key = ""
            for point in info["points"]:
                key = str(point.get("single_key") or "").strip()
                if key:
                    single_key = key
                    break
            if info["point_count"] <= 0 or not single_key:
                continue
            item = {
                "kind_id": brand_row.get("kind_id"),
                "kind_name": brand_row.get("kind_name") or "",
                "brand_id": brand_row.get("brand_id"),
                "brand_name": brand_row.get("brand_name") or "",
                "series_id": row.get("series_id") or 0,
                "series_name": name,
                "series_en_name": en,
                "single_key": single_key,
                "point_count": info["point_count"],
            }
            if is_whole_series(name, en) and int(brand_row.get("brand_id") or 0) == own_id:
                whole.append(item)
                continue
            overlap = len(qset & _grams(blob)) if qset else 0
            if any(word in str(query) for word in ("气垫", "粉底", "垫")) and "气垫" in name:
                overlap += 3
            if any(word in str(query) for word in ("盒", "瓶", "无损")) and any(word in name for word in ("盒", "瓶", "无损")):
                overlap += 3
            exact = norm(query) and (norm(query) in norm(blob) or norm(blob) in norm(query))
            ranked.append((1000 if exact else overlap, int(row.get("series_id") or 0), item))
    ranked.sort(key=lambda item: (-item[0], item[1]))

    def with_whole(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = {int(item.get("series_id") or 0) for item in items}
        out = list(items)
        for item in whole:
            sid = int(item.get("series_id") or 0)
            if sid in seen:
                continue
            seen.add(sid)
            out.append(item)
        return out

    positive = [item for item in ranked if item[0] > 0]
    if positive:
        return with_whole([item[2] for item in positive[:limit]])
    if int(kind.get("kind_id") or 0) == 88 or str(kind.get("kind_name") or "") == "美妆":
        return with_whole(makeup_generic(brand))
    own = [item for item in ranked if int(item[2].get("brand_id") or 0) == own_id] or ranked
    return with_whole([item[2] for item in own[:3]])


def makeup_generic(brand: Dict[str, Any]) -> List[Dict[str, Any]]:
    """没有对上的美妆 AI 系列时，任何品牌都能用的盒子和瓶子。不能走算法多图。"""
    base = {
        "kind_id": 88,
        "kind_name": "美妆",
        "brand_id": brand.get("brand_id"),
        "brand_name": brand.get("brand_name") or "",
        "mode": "single_point",
        "point_count": 5,
    }
    return [
        dict(base, series_id=880002, series_name="全品牌通用盒鉴定", series_en_name="", single_key="outer_packing"),
        dict(base, series_id=880001, series_name="全品牌通用瓶鉴定", series_en_name="", single_key="bottle_all"),
    ]


def points_for(catalog: Dict[str, Any], brand: Dict[str, Any], series: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    bid = int(brand.get("brand_id") or 0)
    hierarchy = "1"
    query_id = bid
    key = "1:%d" % bid
    if series and series.get("is_related"):
        sid = int(series.get("series_id") or 0)
        if sid > 0:
            hierarchy = "2"
            query_id = sid
            key = "2:%d" % sid
    pts = catalog.get("points", {}).get(key) or []
    must = [p for p in pts if int(p.get("must") or 0) == 1]
    return {
        "hierarchy": hierarchy,
        "query_id": query_id,
        "points_key": key,
        "points": pts,
        "must_points": must,
        "point_count": len(pts),
        "must_count": len(must),
    }


def cmd_kinds(args: argparse.Namespace) -> int:
    catalog = load()
    rows = catalog.get("kinds") or []
    if args.q:
        rows = [k for k in rows if text_hit(args.q, k.get("kind_name"), k.get("kind_id"))]
    covered = set(str(x) for x in (catalog.get("covered_kind_ids") or []))
    out = []
    for k in rows:
        kid = str(k.get("kind_id"))
        out.append({
            "kind_id": k.get("kind_id"),
            "kind_name": k.get("kind_name"),
            "covered": kid in covered,
            "brand_count": len((catalog.get("brands") or {}).get(kid) or []),
        })
    emit({"ok": True, "count": len(out), "kinds": out})
    return 0


def cmd_brands(args: argparse.Namespace) -> int:
    catalog = load()
    kind = find_kind(catalog, args.kind, args.kind_id)
    if args.kind or args.kind_id is not None:
        if not kind:
            emit({"ok": False, "error": "品类未找到", "kind": args.kind, "kind_id": args.kind_id})
            return 1
        kid = str(kind["kind_id"])
        rows = list((catalog.get("brands") or {}).get(kid) or [])
    else:
        emit({"ok": False, "error": "请传 --kind 或 --kind-id"})
        return 2
    if args.q:
        rows = [b for b in rows if text_hit(args.q, b.get("brand_name"), b.get("brand_en_name"), b.get("brand_id"))]
    emit({
        "ok": True,
        "kind_id": kind["kind_id"],
        "kind_name": kind.get("kind_name"),
        "count": len(rows),
        "brands": rows,
    })
    return 0


def cmd_series(args: argparse.Namespace) -> int:
    catalog = load()
    kind = find_kind(catalog, args.kind, args.kind_id)
    brand = find_brand(catalog, int(kind["kind_id"]) if kind else args.kind_id, args.brand, args.brand_id)
    if not brand:
        emit({"ok": False, "error": "品牌未找到", "brand": args.brand, "brand_id": args.brand_id})
        return 1
    rows = list((catalog.get("series") or {}).get(str(brand["brand_id"])) or [])
    if args.q:
        rows = [s for s in rows if text_hit(args.q, s.get("series_name"), s.get("series_en_name"), s.get("series_id"))]
    emit({
        "ok": True,
        "kind_id": brand.get("kind_id"),
        "brand_id": brand.get("brand_id"),
        "brand_name": brand.get("brand_name"),
        "count": len(rows),
        "series": rows,
    })
    return 0


def cmd_points(args: argparse.Namespace) -> int:
    catalog = load()
    kind = find_kind(catalog, args.kind, args.kind_id)
    brand = find_brand(catalog, int(kind["kind_id"]) if kind else args.kind_id, args.brand, args.brand_id)
    if not brand:
        emit({"ok": False, "error": "品牌未找到"})
        return 1
    series = find_series(catalog, int(brand["brand_id"]), args.series, args.series_id)
    info = points_for(catalog, brand, series)
    pts = info["points"]
    if args.q:
        pts = [p for p in pts if text_hit(args.q, p.get("point_name"), p.get("point_id"), p.get("single_key"))]
        info = dict(info)
        info["points"] = pts
        info["point_count"] = len(pts)
        info["must_points"] = [p for p in pts if int(p.get("must") or 0) == 1]
        info["must_count"] = len(info["must_points"])
    emit({
        "ok": True,
        "kind_id": brand.get("kind_id"),
        "brand_id": brand.get("brand_id"),
        "brand_name": brand.get("brand_name"),
        "series_id": (series or {}).get("series_id"),
        "series_name": (series or {}).get("series_name"),
        "series_is_related": bool((series or {}).get("is_related")),
        **info,
    })
    return 0


def cmd_must(args: argparse.Namespace) -> int:
    args.q = None
    # reuse points then trim
    catalog = load()
    kind = find_kind(catalog, args.kind, args.kind_id)
    brand = find_brand(catalog, int(kind["kind_id"]) if kind else args.kind_id, args.brand, args.brand_id)
    if not brand:
        emit({"ok": False, "error": "品牌未找到"})
        return 1
    series = find_series(catalog, int(brand["brand_id"]), args.series, args.series_id)
    info = points_for(catalog, brand, series)
    emit({
        "ok": True,
        "brand_id": brand.get("brand_id"),
        "series_id": (series or {}).get("series_id"),
        "must_count": info["must_count"],
        "must_points": info["must_points"],
    })
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    catalog = load()
    kind = find_kind(catalog, args.kind, args.kind_id)
    if not kind:
        emit({"ok": False, "error": "品类未找到", "hint": "python catalog_query.py kinds --q ..."})
        return 1
    brand = find_brand(catalog, int(kind["kind_id"]), args.brand, args.brand_id)
    if not brand:
        emit({"ok": False, "error": "品牌未找到", "kind_id": kind["kind_id"]})
        return 1
    series = find_series(catalog, int(brand["brand_id"]), args.series, args.series_id)
    if series is None and not args.series and args.series_id is None:
        rows = list((catalog.get("series") or {}).get(str(brand["brand_id"])) or [])
        product = [row for row in rows if row.get("is_related") and not _makeup_fallback_series(row)]
        if len(product) > 1:
            emit({
                "ok": False,
                "error": "这个品牌有多个系列，请按图片选定系列后再 resolve",
                "kind_id": kind["kind_id"],
                "kind_name": kind.get("kind_name"),
                "brand_id": brand.get("brand_id"),
                "brand_name": brand.get("brand_name"),
                "series": [
                    {
                        "series_id": row.get("series_id"),
                        "series_name": row.get("series_name") or "",
                        "series_en_name": row.get("series_en_name") or "",
                    }
                    for row in product
                ],
            })
            return 1
    info = points_for(catalog, brand, series)
    single_key = ""
    for p in info["points"]:
        key = str(p.get("single_key") or "").strip()
        if key:
            single_key = key
            break
    emit({
        "ok": True,
        "single_key": single_key,
        "kind_id": kind["kind_id"],
        "kind_name": kind.get("kind_name"),
        "brand_id": brand.get("brand_id"),
        "brand_name": brand.get("brand_name"),
        "brand_en_name": brand.get("brand_en_name"),
        "brand_is_related": bool(brand.get("is_related")),
        "series_id": (series or {}).get("series_id") or 0,
        "series_name": (series or {}).get("series_name") or "",
        "series_en_name": (series or {}).get("series_en_name") or "",
        "series_is_related": bool((series or {}).get("is_related")),
        "hierarchy": info["hierarchy"],
        "query_id": info["query_id"],
        "point_count": info["point_count"],
        "must_count": info["must_count"],
        "must_points": [
            {"point_id": p.get("point_id"), "point_name": p.get("point_name")}
            for p in info["must_points"]
        ],
        "points": [
            {"point_id": p.get("point_id"), "point_name": p.get("point_name"), "must": int(p.get("must") or 0)}
            for p in info["points"]
        ],
    })
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="鉴X本地目录查询")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_scope(sp: argparse.ArgumentParser, series: bool = False) -> None:
        sp.add_argument("--kind")
        sp.add_argument("--kind-id", type=int)
        sp.add_argument("--brand")
        sp.add_argument("--brand-id", type=int)
        if series:
            sp.add_argument("--series")
            sp.add_argument("--series-id", type=int)
        sp.add_argument("--q", help="名称模糊过滤")

    sk = sub.add_parser("kinds", help="品类列表")
    sk.add_argument("--q")
    sk.set_defaults(func=cmd_kinds)

    sb = sub.add_parser("brands", help="品牌列表")
    add_scope(sb)
    sb.set_defaults(func=cmd_brands)

    ss = sub.add_parser("series", help="系列列表")
    add_scope(ss)
    ss.set_defaults(func=cmd_series)

    sp = sub.add_parser("points", help="点位列表")
    add_scope(sp, series=True)
    sp.set_defaults(func=cmd_points)

    sm = sub.add_parser("must", help="必拍点")
    add_scope(sm, series=True)
    sm.set_defaults(func=cmd_must)

    sr = sub.add_parser("resolve", help="品类+品牌+系列一次解析到点位")
    add_scope(sr, series=True)
    sr.set_defaults(func=cmd_resolve)
    return p


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
