#!/usr/bin/env python3
"""Join the NSI registry with OSM polygons and emit the final GeoJSON.

Inputs
  territorial_formations.csv  — produced by fetch_nsi.py
  data/osm_raw.json           — produced by fetch_osm.py
  ../settlements.geojson      — parent settlement polygons (for parent bbox)
  ../municipalities.geojson   — parent obshtina polygons (NUTS4 fallback bbox)
  ../municipalities.csv       — municipality EKATTE → NUTS4 mapping

Outputs
  territorial_formations.geojson   — sorted by ekatte, fixed precision
  data/match_log.csv               — per-record decision audit trail

Match priority
  1. ref:ekatte tag — confidence 1.0
  2. exact normalized name + parent bbox — confidence 0.9
  3. fuzzy (max of token_set_ratio, partial_ratio) >= 85 + parent bbox —
     confidence = score / 100

Names ending in a digit (e.g. "Бизнес парк Бургас 1") require an exact
match — fuzzy is disabled to prevent collapsing numbered series.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from rapidfuzz import fuzz
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import unary_union
from shapely.validation import make_valid

ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ROOT.parent
DATA_DIR = ROOT / "data"

NSI_CSV = ROOT / "territorial_formations.csv"
OSM_RAW = DATA_DIR / "osm_raw.json"
OUT_GEOJSON = ROOT / "territorial_formations.geojson"
MATCH_LOG = DATA_DIR / "match_log.csv"

PARENT_SETTLEMENTS = REPO_ROOT / "settlements.geojson"
PARENT_MUNICIPALITIES = REPO_ROOT / "municipalities.geojson"
MUNICIPALITIES_CSV = REPO_ROOT / "municipalities.csv"

COORD_PRECISION = 6
FUZZY_THRESHOLD = 85
ENDS_WITH_DIGIT = re.compile(r"\d\s*$")
QUOTE_CHARS = "\"'„“”«»‘’`´"
WS_RE = re.compile(r"\s+")


# ---------- normalization ----------------------------------------------------


def normalize_name(value: str | None) -> str:
    if not value:
        return ""
    s = unicodedata.normalize("NFC", value)
    s = s.translate({ord(c): " " for c in QUOTE_CHARS})
    s = s.replace(" ", " ")
    s = WS_RE.sub(" ", s).strip().lower()
    return s


# ---------- parent bbox lookup -----------------------------------------------


def _bbox_of(geom_obj: dict) -> tuple[float, float, float, float]:
    g = shape(geom_obj)
    minx, miny, maxx, maxy = g.bounds
    return minx, miny, maxx, maxy


def load_parent_index() -> dict[str, tuple[float, float, float, float]]:
    """Build EKATTE/NUTS4 → bbox lookup from sibling GeoJSON layers."""
    idx: dict[str, tuple[float, float, float, float]] = {}

    if PARENT_SETTLEMENTS.exists():
        with PARENT_SETTLEMENTS.open("r", encoding="utf-8") as f:
            gj = json.load(f)
        for feat in gj.get("features", []):
            ek = str(feat.get("properties", {}).get("ekatte", "")).strip()
            if ek:
                idx[ek] = _bbox_of(feat["geometry"])
        print(f"  parent index: {len(idx)} settlement bboxes", file=sys.stderr)

    if PARENT_MUNICIPALITIES.exists():
        with PARENT_MUNICIPALITIES.open("r", encoding="utf-8") as f:
            gj = json.load(f)
        for feat in gj.get("features", []):
            n4 = str(feat.get("properties", {}).get("nuts4", "")).strip()
            if n4:
                idx[n4] = _bbox_of(feat["geometry"])

    # Map municipality EKATTE → NUTS4 so we can resolve when parent_ekatte
    # points at the obshtina centre (5-digit) rather than the NUTS4 code.
    if MUNICIPALITIES_CSV.exists():
        with MUNICIPALITIES_CSV.open("r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ek = (row.get("ekatte") or "").strip()
                code = (row.get("code") or "").strip()
                if ek and code and code in idx and ek not in idx:
                    idx[ek] = idx[code]

    print(f"  parent index total: {len(idx)} entries", file=sys.stderr)
    return idx


def _bbox_contains(b: tuple[float, float, float, float], x: float, y: float, pad: float = 0.0) -> bool:
    return (b[0] - pad) <= x <= (b[2] + pad) and (b[1] - pad) <= y <= (b[3] + pad)


# ---------- OSM element → polygon -------------------------------------------


def _round_coords(geom_obj: Any, precision: int) -> Any:
    """Recursively round numeric coordinates to fixed precision."""
    if isinstance(geom_obj, list):
        return [_round_coords(x, precision) for x in geom_obj]
    if isinstance(geom_obj, tuple):
        return tuple(_round_coords(x, precision) for x in geom_obj)
    if isinstance(geom_obj, float):
        return round(geom_obj, precision)
    return geom_obj


def _ring_from_geometry(nodes: list[dict]) -> list[tuple[float, float]]:
    return [(n["lon"], n["lat"]) for n in nodes if "lon" in n and "lat" in n]


def _polygon_from_way(el: dict) -> Polygon | None:
    nodes = el.get("geometry") or []
    ring = _ring_from_geometry(nodes)
    if len(ring) < 4:
        return None
    if ring[0] != ring[-1]:
        return None  # not closed → not a polygon
    try:
        poly = Polygon(ring)
        if not poly.is_valid:
            poly = make_valid(poly)
        if poly.is_empty:
            return None
        return poly
    except Exception:
        return None


def _multipolygon_from_relation(el: dict) -> Polygon | MultiPolygon | None:
    """Assemble a (multi)polygon from a relation's `members` (Overpass `out geom`).

    We use a tolerant approach: collect outer rings (closed) and inner rings,
    union outers, subtract inners. Open ways get stitched best-effort.
    """
    members = el.get("members") or []
    outer_rings: list[Polygon] = []
    inner_rings: list[Polygon] = []
    open_outer: list[list[tuple[float, float]]] = []
    open_inner: list[list[tuple[float, float]]] = []

    for m in members:
        if m.get("type") != "way":
            continue
        role = (m.get("role") or "outer").lower()
        coords = _ring_from_geometry(m.get("geometry") or [])
        if len(coords) < 2:
            continue
        if coords[0] == coords[-1] and len(coords) >= 4:
            try:
                p = Polygon(coords)
                if not p.is_valid:
                    p = make_valid(p)
                if p.is_empty:
                    continue
                if role == "inner":
                    inner_rings.append(p)
                else:
                    outer_rings.append(p)
            except Exception:
                continue
        else:
            (open_inner if role == "inner" else open_outer).append(coords)

    for stitched in _stitch_rings(open_outer):
        try:
            p = Polygon(stitched)
            if not p.is_valid:
                p = make_valid(p)
            if not p.is_empty:
                outer_rings.append(p)
        except Exception:
            pass
    for stitched in _stitch_rings(open_inner):
        try:
            p = Polygon(stitched)
            if not p.is_valid:
                p = make_valid(p)
            if not p.is_empty:
                inner_rings.append(p)
        except Exception:
            pass

    if not outer_rings:
        return None
    outer = unary_union(outer_rings)
    if inner_rings:
        outer = outer.difference(unary_union(inner_rings))
    if outer.is_empty:
        return None
    if isinstance(outer, (Polygon, MultiPolygon)):
        return outer
    # GeometryCollection or similar — pull polygons out
    polys = [g for g in getattr(outer, "geoms", []) if isinstance(g, Polygon)]
    if not polys:
        return None
    return MultiPolygon(polys) if len(polys) > 1 else polys[0]


def _stitch_rings(segments: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Best-effort stitching of open way segments into closed rings."""
    rings: list[list[tuple[float, float]]] = []
    pool = [list(s) for s in segments]
    while pool:
        cur = pool.pop()
        changed = True
        while changed:
            changed = False
            for i, seg in enumerate(pool):
                if cur[-1] == seg[0]:
                    cur.extend(seg[1:])
                    pool.pop(i)
                    changed = True
                    break
                if cur[-1] == seg[-1]:
                    cur.extend(reversed(seg[:-1]))
                    pool.pop(i)
                    changed = True
                    break
                if cur[0] == seg[-1]:
                    cur = list(seg) + cur[1:]
                    pool.pop(i)
                    changed = True
                    break
                if cur[0] == seg[0]:
                    cur = list(reversed(seg)) + cur[1:]
                    pool.pop(i)
                    changed = True
                    break
        if len(cur) >= 4 and cur[0] == cur[-1]:
            rings.append(cur)
    return rings


def osm_to_polygon(el: dict) -> Polygon | MultiPolygon | None:
    t = el.get("type")
    if t == "way":
        return _polygon_from_way(el)
    if t == "relation":
        return _multipolygon_from_relation(el)
    return None


# ---------- candidate index --------------------------------------------------


class Candidate:
    __slots__ = ("osm_type", "osm_id", "tags", "geom", "centroid", "name_norm", "name_bg_norm")

    def __init__(
        self,
        osm_type: str,
        osm_id: int,
        tags: dict[str, str],
        geom: Polygon | MultiPolygon,
    ):
        self.osm_type = osm_type
        self.osm_id = osm_id
        self.tags = tags
        self.geom = geom
        c = geom.centroid
        self.centroid = (c.x, c.y)
        self.name_norm = normalize_name(tags.get("name") or "")
        self.name_bg_norm = normalize_name(tags.get("name:bg") or "")


def load_candidates(path: Path) -> list[Candidate]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    elements = data.get("elements", [])
    cands: list[Candidate] = []
    skipped_no_geom = 0
    for el in elements:
        if el.get("type") not in ("way", "relation"):
            continue
        tags = el.get("tags") or {}
        geom = osm_to_polygon(el)
        if geom is None:
            skipped_no_geom += 1
            continue
        cands.append(Candidate(el["type"], int(el["id"]), tags, geom))
    print(
        f"  OSM candidates: {len(cands)} polygons "
        f"(skipped {skipped_no_geom} non-polygonal)",
        file=sys.stderr,
    )
    return cands


# ---------- matching ---------------------------------------------------------


def build_indexes(cands: list[Candidate]) -> tuple[
    dict[str, list[Candidate]],   # ref:ekatte → cands
    dict[str, list[Candidate]],   # normalized name → cands
]:
    by_ref: dict[str, list[Candidate]] = defaultdict(list)
    by_name: dict[str, list[Candidate]] = defaultdict(list)
    for c in cands:
        ref = c.tags.get("ref:ekatte")
        if ref:
            by_ref[ref.strip()].append(c)
        if c.name_norm:
            by_name[c.name_norm].append(c)
        if c.name_bg_norm and c.name_bg_norm != c.name_norm:
            by_name[c.name_bg_norm].append(c)
    print(
        f"  index: {len(by_ref)} ref:ekatte keys, {len(by_name)} name keys",
        file=sys.stderr,
    )
    return by_ref, by_name


def _within_parent(
    cand: Candidate,
    parent_ekatte: str,
    area2_ekatte: str,
    parent_index: dict[str, tuple[float, float, float, float]],
) -> bool:
    """Centroid must lie inside parent bbox (or area2's bbox if dual-parented)."""
    bboxes = []
    for code in (parent_ekatte, area2_ekatte):
        if code and code in parent_index:
            bboxes.append(parent_index[code])
    if not bboxes:
        # No parent reference — accept (country-wide search)
        return True
    x, y = cand.centroid
    return any(_bbox_contains(b, x, y, pad=0.05) for b in bboxes)


def match_record(
    rec: dict[str, str],
    by_ref: dict[str, list[Candidate]],
    by_name: dict[str, list[Candidate]],
    all_cands: list[Candidate],
    parent_index: dict[str, tuple[float, float, float, float]],
) -> tuple[Candidate | None, str, float, str]:
    """Return (cand, method, confidence, reason)."""
    ekatte = rec["ekatte"]
    name_bg = rec["name_bg"]
    parent_ekatte = rec["parent_ekatte"]
    area2_ekatte = rec["area2_ekatte"]

    # 1) ref:ekatte exact
    refs = by_ref.get(ekatte, [])
    if refs:
        if len(refs) == 1:
            return refs[0], "ref_ekatte", 1.0, "single ref:ekatte hit"
        # Multiple — pick largest area, log ambiguity
        best = max(refs, key=lambda c: c.geom.area)
        return best, "ref_ekatte", 1.0, f"multiple ref:ekatte hits ({len(refs)}); kept largest"

    nname = normalize_name(name_bg)
    if not nname:
        return None, "none", 0.0, "empty NSI name"

    # 2) exact name within parent bbox
    exact = [c for c in by_name.get(nname, []) if _within_parent(c, parent_ekatte, area2_ekatte, parent_index)]
    if exact:
        if len(exact) == 1:
            return exact[0], "name_exact", 0.9, "exact name + parent bbox"
        best = max(exact, key=lambda c: c.geom.area)
        return best, "name_exact", 0.9, f"exact name, {len(exact)} candidates; kept largest"

    # 3) fuzzy — disabled when name ends in a digit (numbered series)
    if ENDS_WITH_DIGIT.search(name_bg):
        return None, "none", 0.0, "no exact match; fuzzy disabled (numbered name)"

    # NSI names are usually decorated ("Курортен комплекс \"Слънчев бряг\"")
    # while OSM carries the bare place name ("Слънчев бряг"). token_set_ratio
    # undercounts that case (~50–60), so we also try partial_ratio, which
    # finds the best substring alignment of the shorter inside the longer.
    # parent_bbox is the spatial guard against accidental hits.
    scored: list[tuple[float, Candidate]] = []
    for c in all_cands:
        n = c.name_norm or c.name_bg_norm
        if not n or len(n) < 4:
            # Reject ultra-short OSM names — they substring-match almost anything.
            continue
        score = max(
            fuzz.token_set_ratio(nname, n),
            fuzz.partial_ratio(nname, n),
        )
        if score >= FUZZY_THRESHOLD and _within_parent(c, parent_ekatte, area2_ekatte, parent_index):
            scored.append((score, c))
    if not scored:
        return None, "none", 0.0, "no candidate within parent bbox"

    scored.sort(key=lambda t: (-t[0], -t[1].geom.area))
    top_score = scored[0][0]
    top_tier = [c for s, c in scored if s == top_score]
    best = max(top_tier, key=lambda c: c.geom.area)
    note = "fuzzy match"
    if len(top_tier) > 1:
        note = f"fuzzy match; tied at {top_score} ({len(top_tier)} cands); kept largest"
    return best, "name_fuzzy", round(top_score / 100.0, 3), note


# ---------- output -----------------------------------------------------------


def make_feature(rec: dict[str, str], cand: Candidate, method: str, conf: float) -> dict:
    geom = mapping(cand.geom)
    geom["coordinates"] = _round_coords(geom["coordinates"], COORD_PRECISION)
    props = {k: rec[k] for k in (
        "ekatte", "kind", "name_bg", "name_en",
        "parent_ekatte", "parent_name", "parent_is_nuts4",
        "area2_ekatte", "area2_name", "document",
    )}
    props["osm_type"] = cand.osm_type
    props["osm_id"] = cand.osm_id
    props["match_method"] = method
    props["match_confidence"] = conf
    return {"type": "Feature", "properties": props, "geometry": geom}


def write_geojson(features: list[dict], path: Path) -> None:
    """Match the repo's settlements.geojson style: compact, one feature per line."""
    features = sorted(features, key=lambda f: f["properties"]["ekatte"])
    sep = (",", ":")
    with path.open("w", encoding="utf-8") as f:
        f.write('{"type":"FeatureCollection","features":[\n')
        for i, feat in enumerate(features):
            if i:
                f.write(",\n")
            json.dump(feat, f, ensure_ascii=False, separators=sep)
        f.write("\n]}\n")


def write_log(rows: Iterable[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["ekatte", "name_bg", "kind", "decision", "osm_type", "osm_id", "score", "reason"]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def report(records: list[dict[str, str]], log: list[dict[str, Any]]) -> None:
    total = len(records)
    by_method: dict[str, int] = defaultdict(int)
    by_kind_total: dict[str, int] = defaultdict(int)
    by_kind_matched: dict[str, int] = defaultdict(int)
    by_oblast_total: dict[str, int] = defaultdict(int)
    by_oblast_matched: dict[str, int] = defaultdict(int)

    matched_ekatte = {l["ekatte"] for l in log if l["decision"] != "none"}

    for r in records:
        kind = r["kind"] or "?"
        by_kind_total[kind] += 1
        if r["ekatte"] in matched_ekatte:
            by_kind_matched[kind] += 1
        # oblast bucket: first 3 letters of NUTS4 if available
        oblast = (r["parent_ekatte"][:3] if r["parent_is_nuts4"] == "1" else "")
        if oblast:
            by_oblast_total[oblast] += 1
            if r["ekatte"] in matched_ekatte:
                by_oblast_matched[oblast] += 1

    for entry in log:
        by_method[entry["decision"]] += 1

    print("\n==== match report ====", file=sys.stderr)
    print(f"  total NSI records:    {total}", file=sys.stderr)
    print(f"  matched:              {len(matched_ekatte)} ({100*len(matched_ekatte)/max(total,1):.1f}%)", file=sys.stderr)
    for k in ("ref_ekatte", "name_exact", "name_fuzzy", "none"):
        print(f"    {k:11s} {by_method.get(k,0)}", file=sys.stderr)

    print("  by kind (matched / total):", file=sys.stderr)
    for k, tot in sorted(by_kind_total.items()):
        print(f"    kind={k}: {by_kind_matched.get(k,0)} / {tot}", file=sys.stderr)

    if by_oblast_total:
        print("  by oblast (matched / total):", file=sys.stderr)
        for k in sorted(by_oblast_total):
            print(f"    {k}: {by_oblast_matched.get(k,0)} / {by_oblast_total[k]}", file=sys.stderr)

    # National-significance gaps — these are the highest priority
    gaps_n = [r for r in records if r["kind"] == "1" and r["ekatte"] not in matched_ekatte]
    if gaps_n:
        print(f"\n  unmatched national-significance (kind=1) — {len(gaps_n)}:", file=sys.stderr)
        for r in sorted(gaps_n, key=lambda x: x["ekatte"]):
            print(f"    {r['ekatte']}  {r['name_bg']}  ({r['parent_name']})", file=sys.stderr)


# ---------- main -------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--nsi", default=str(NSI_CSV), help="NSI CSV input")
    p.add_argument("--osm", default=str(OSM_RAW), help="Overpass JSON input")
    p.add_argument("--out", default=str(OUT_GEOJSON), help="GeoJSON output")
    p.add_argument("--log", default=str(MATCH_LOG), help="match log CSV")
    args = p.parse_args()

    nsi_path = Path(args.nsi)
    osm_path = Path(args.osm)

    if not nsi_path.exists():
        print(f"ERROR: missing {nsi_path} — run fetch_nsi.py first", file=sys.stderr)
        return 2
    if not osm_path.exists():
        print(f"ERROR: missing {osm_path} — run fetch_osm.py first", file=sys.stderr)
        return 2

    with nsi_path.open("r", encoding="utf-8") as f:
        records = list(csv.DictReader(f))
    print(f"loaded {len(records)} NSI records", file=sys.stderr)

    parent_index = load_parent_index()
    cands = load_candidates(osm_path)
    by_ref, by_name = build_indexes(cands)

    features: list[dict] = []
    log: list[dict[str, Any]] = []
    for rec in records:
        cand, method, conf, reason = match_record(rec, by_ref, by_name, cands, parent_index)
        entry = {
            "ekatte": rec["ekatte"],
            "name_bg": rec["name_bg"],
            "kind": rec["kind"],
            "decision": method,
            "osm_type": cand.osm_type if cand else "",
            "osm_id": cand.osm_id if cand else "",
            "score": f"{conf:.3f}",
            "reason": reason,
        }
        log.append(entry)
        if cand:
            features.append(make_feature(rec, cand, method, conf))

    write_geojson(features, Path(args.out))
    write_log(log, Path(args.log))
    print(f"wrote {args.out} ({len(features)} features)", file=sys.stderr)
    print(f"wrote {args.log} ({len(log)} entries)", file=sys.stderr)
    report(records, log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
