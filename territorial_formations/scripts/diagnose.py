#!/usr/bin/env python3
"""Diagnose why a specific NSI record didn't match.

Usage:
  python3 scripts/diagnose.py                # all unmatched kind=1 records
  python3 scripts/diagnose.py 98212 94015    # specific EKATTE codes
  python3 scripts/diagnose.py --grep слънчев # search OSM names

For each record, prints:
  * the parent bbox we resolved
  * how many OSM polygons fall inside that bbox
  * the top-10 fuzzy candidates anywhere in BG (regardless of bbox)
  * for the top-3, the full OSM tag dump

Also lists OSM elements (any geometry, including nodes/open ways) whose
name contains the search token — useful when the matcher's polygon-only
filter discards relevant features.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parent.parent
NSI_CSV = ROOT / "territorial_formations.csv"
OSM_RAW = ROOT / "data" / "osm_raw.json"

sys.path.insert(0, str(ROOT / "scripts"))
from match import (
    Candidate,
    load_candidates,
    load_parent_index,
    normalize_name,
    osm_to_polygon,
)


def load_records() -> list[dict[str, str]]:
    with NSI_CSV.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def grep_raw(token: str) -> None:
    """Print every OSM element whose name (any variant) contains token."""
    with OSM_RAW.open("r", encoding="utf-8") as f:
        data = json.load(f)
    needle = token.lower()
    hits = []
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        for k, v in tags.items():
            if k.startswith("name") and needle in str(v).lower():
                hits.append((el.get("type"), el.get("id"), tags))
                break
    print(f"--- raw grep: '{token}' → {len(hits)} elements ---")
    for t, i, tags in hits[:30]:
        name = tags.get("name") or tags.get("name:bg") or ""
        place = tags.get("place") or tags.get("boundary") or tags.get("tourism") or tags.get("leisure") or ""
        print(f"  {t:<8} {i:>12}  place/boundary={place:<18}  name={name!r}")
        if t == "node":
            print(f"    (node — no polygon)")


def diagnose_record(rec: dict[str, str], cands: list[Candidate], parent_index) -> None:
    ek = rec["ekatte"]
    name_bg = rec["name_bg"]
    pe = rec["parent_ekatte"]
    nname = normalize_name(name_bg)
    pbbox = parent_index.get(pe)

    print(f"\n=== {ek}  {name_bg}  parent={pe} ({rec['parent_name']}) ===")
    print(f"  normalized NSI name: {nname!r}")
    if pbbox:
        print(f"  parent bbox: {pbbox}")
        in_bbox = sum(
            1
            for c in cands
            if pbbox[0] <= c.centroid[0] <= pbbox[2] and pbbox[1] <= c.centroid[1] <= pbbox[3]
        )
        print(f"  OSM polygons inside parent bbox: {in_bbox}")
    else:
        print("  parent bbox: NOT FOUND in parent index — country-wide search")

    scored = []
    for c in cands:
        n = c.name_norm or c.name_bg_norm
        if not n:
            continue
        score = max(fuzz.token_set_ratio(nname, n), fuzz.partial_ratio(nname, n))
        in_b = (
            pbbox is None
            or (pbbox[0] <= c.centroid[0] <= pbbox[2] and pbbox[1] <= c.centroid[1] <= pbbox[3])
        )
        scored.append((score, in_b, c))
    scored.sort(key=lambda t: (-t[0], -t[2].geom.area))

    print(f"  top-10 candidates by fuzzy score (★ = inside parent bbox):")
    for score, in_b, c in scored[:10]:
        flag = "★" if in_b else " "
        print(
            f"   {flag} {score:6.2f}  {c.osm_type}/{c.osm_id:<12}  "
            f"name={(c.tags.get('name') or '')[:40]!r}"
        )

    if scored:
        print("  top-3 full tags:")
        for score, in_b, c in scored[:3]:
            print(f"    [{c.osm_type}/{c.osm_id}] score={score}")
            for k, v in sorted(c.tags.items()):
                print(f"      {k}={v}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("ekatte", nargs="*", help="Specific EKATTE codes to diagnose")
    p.add_argument("--grep", help="Search OSM raw for elements with this name token")
    args = p.parse_args()

    if args.grep:
        grep_raw(args.grep)
        return 0

    records = load_records()
    parent_index = load_parent_index()
    cands = load_candidates(OSM_RAW)

    if args.ekatte:
        wanted = set(args.ekatte)
        targets = [r for r in records if r["ekatte"] in wanted]
    else:
        targets = [r for r in records if r["kind"] == "1"]

    for rec in targets:
        diagnose_record(rec, cands, parent_index)
    return 0


if __name__ == "__main__":
    sys.exit(main())
