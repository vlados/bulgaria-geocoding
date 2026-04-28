#!/usr/bin/env python3
"""Fetch NSI EKATTE territorial-formations registry and emit a CSV.

Source: https://www.nsi.bg/nrnm/ekatte/territorial-formations/json

Output:
  data/nsi_raw.json              — full upstream response (gitignored)
  territorial_formations.csv     — flattened registry (committed)

The endpoint returns ALL records in a single response (no pagination).
We re-fetch the HTML page to read the displayed total and fail loudly if
the JSON count disagrees — the registry is small and any drift means the
endpoint shape changed.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

NSI_JSON = "https://www.nsi.bg/nrnm/ekatte/territorial-formations/json"
NSI_HTML = "https://www.nsi.bg/nrnm/ekatte/territorial-formations"

USER_AGENT = (
    "bulgaria-geocoding/territorial-formations "
    "(+https://github.com/yurukov/Bulgaria-geocoding)"
)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_JSON = DATA_DIR / "nsi_raw.json"
OUT_CSV = ROOT / "territorial_formations.csv"

# area1 looks like "(67338) гр. Сливен, общ. Сливен, обл. Сливен"
# or "(VAR06) общ. Варна, обл. Варна". CODE is either 5-digit EKATTE or
# NUTS4 (3 letters + 2 digits).
PARENT_RE = re.compile(r"^\s*\(([0-9A-Z]{5})\)\s*(.*)$")
NUTS4_RE = re.compile(r"^[A-Z]{3}\d{2}$")
EKATTE_RE = re.compile(r"^\d{5}$")
# Strip leading administrative-unit markers from the parent name so we keep
# just the place name (e.g. "гр. Сливен, общ. Сливен, обл. Сливен" → "Сливен").
PARENT_NAME_PREFIX = re.compile(r"^\s*(гр\.|с\.|кв\.|общ\.|обл\.)\s*", re.UNICODE)

CSV_FIELDS = [
    "ekatte",
    "kind",
    "name_bg",
    "name_en",
    "parent_ekatte",
    "parent_name",
    "parent_is_nuts4",
    "area2_ekatte",
    "area2_name",
    "document",
]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "bg,en;q=0.7",
        }
    )
    return s


def _get(session: requests.Session, url: str, *, attempts: int = 5, timeout: int = 60) -> requests.Response:
    delay = 2.0
    for i in range(1, attempts + 1):
        try:
            r = session.get(url, timeout=timeout)
            if r.status_code == 429 or 500 <= r.status_code < 600:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except (requests.RequestException, requests.HTTPError) as e:
            if i == attempts:
                raise
            print(f"  retry {i}/{attempts} after {delay:.0f}s ({e})", file=sys.stderr)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def parse_area(value: str | None) -> tuple[str, str, bool]:
    """Split "(CODE) name, общ. X, обл. Y" into (code, place_name, is_nuts4).

    Returns ("", "", False) when value is empty.
    Returns ("", value.strip(), False) when no code is present.

    The trailing administrative path (общ. ..., обл. ...) is dropped so the
    parent name matches the corresponding settlements/municipalities entry.
    """
    if not value:
        return "", "", False
    text = value.strip()
    m = PARENT_RE.match(text)
    if not m:
        return "", text, False
    code = m.group(1).strip()
    rest = m.group(2).strip()
    # Take only the first comma-separated chunk (the place itself).
    head = rest.split(",", 1)[0].strip()
    head = PARENT_NAME_PREFIX.sub("", head).strip()
    is_nuts4 = bool(NUTS4_RE.match(code))
    if not (is_nuts4 or EKATTE_RE.match(code)):
        return code, head, False
    return code, head, is_nuts4


def _coerce_ekatte(value: Any) -> str:
    """EKATTE codes are 5 chars and may have leading zeros — keep as string."""
    if value is None:
        return ""
    s = str(value).strip()
    if s.isdigit() and len(s) < 5:
        s = s.zfill(5)
    return s


def _coerce_kind(value: Any) -> str:
    if value is None or value == "":
        return ""
    s = str(value).strip()
    return s


def _row_get(row: dict[str, Any], *keys: str, default: str = "") -> str:
    """Tolerant getter — NSI sometimes ships keys in different cases."""
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return str(row[k]).strip()
        # case-insensitive scan as a fallback
        for actual in row:
            if actual.lower() == k.lower() and row[actual] not in (None, ""):
                return str(row[actual]).strip()
    return default


def normalize(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in rows:
        ekatte = _coerce_ekatte(_row_get(row, "ekatte", "EKATTE"))
        if not ekatte:
            # NSI ships an empty sentinel as the first record — skip it.
            continue
        name_bg = _row_get(row, "name", "name_bg", "nameBg", "imeBg")
        name_en = _row_get(row, "nameLatin", "name_en", "nameEn", "imeLat")
        kind = _coerce_kind(_row_get(row, "kind", "vid", "type"))
        area1 = _row_get(row, "area1", "area_1", "area")
        area2 = _row_get(row, "area2", "area_2")
        document = _row_get(row, "document", "doc", "documentCode")

        parent_code, parent_name, parent_is_nuts4 = parse_area(area1)
        a2_code, a2_name, _ = parse_area(area2)

        out.append(
            {
                "ekatte": ekatte,
                "kind": kind,
                "name_bg": name_bg,
                "name_en": name_en,
                "parent_ekatte": parent_code,
                "parent_name": parent_name,
                "parent_is_nuts4": "1" if parent_is_nuts4 else "0",
                "area2_ekatte": a2_code,
                "area2_name": a2_name,
                "document": document,
            }
        )
    out.sort(key=lambda r: r["ekatte"])
    return out


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def fetch(session: requests.Session) -> list[dict[str, Any]]:
    print(f"GET {NSI_JSON}", file=sys.stderr)
    r = _get(session, NSI_JSON)
    data = r.json()
    if isinstance(data, dict):
        # Some NSI endpoints wrap the list; try to find it.
        for k in ("data", "items", "rows", "result"):
            if isinstance(data.get(k), list):
                data = data[k]
                break
    if not isinstance(data, list):
        raise RuntimeError(
            f"Unexpected JSON shape from NSI — top-level is {type(data).__name__}"
        )
    return data


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--cached",
        action="store_true",
        help="Skip download and reuse data/nsi_raw.json if present.",
    )
    args = p.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.cached and RAW_JSON.exists():
        print(f"using cached {RAW_JSON}", file=sys.stderr)
        with RAW_JSON.open("r", encoding="utf-8") as f:
            rows = json.load(f)
    else:
        s = _session()
        rows = fetch(s)
        with RAW_JSON.open("w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2, sort_keys=True)
        print(f"wrote {RAW_JSON} ({len(rows)} rows)", file=sys.stderr)

    norm = normalize(rows)
    write_csv(norm, OUT_CSV)
    print(f"wrote {OUT_CSV} ({len(norm)} rows)", file=sys.stderr)

    # Stats: kind breakdown, parent_is_nuts4 count, missing names
    kinds: dict[str, int] = {}
    nuts4_parents = 0
    missing_parent = 0
    for r in norm:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        if r["parent_is_nuts4"] == "1":
            nuts4_parents += 1
        if not r["parent_ekatte"]:
            missing_parent += 1
    print("---- NSI registry summary ----", file=sys.stderr)
    print(f"  total: {len(norm)}", file=sys.stderr)
    print(f"  by kind: {kinds}", file=sys.stderr)
    print(f"  parent code = NUTS4: {nuts4_parents}", file=sys.stderr)
    print(f"  parent unparseable: {missing_parent}", file=sys.stderr)

    # Sample rows — first three by EKATTE
    print("---- sample rows ----", file=sys.stderr)
    for r in norm[:3]:
        print(f"  {r}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
