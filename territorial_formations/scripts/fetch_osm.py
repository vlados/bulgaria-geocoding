#!/usr/bin/env python3
"""Run a single Overpass query covering all candidate territorial formations.

Output:
  data/osm_raw.json — raw Overpass response (gitignored, ~50–200 MB)

Etiquette:
  * Polite User-Agent, single request
  * Honor 429 / 504 with exponential backoff
  * 300s server-side timeout, 600s client timeout
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

USER_AGENT = (
    "bulgaria-geocoding/territorial-formations "
    "(+https://github.com/yurukov/Bulgaria-geocoding)"
)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_OSM = DATA_DIR / "osm_raw.json"

QUERY = r"""
[out:json][timeout:300];
area["ISO3166-1"="BG"][admin_level=2]->.bg;
(
  // Direct EKATTE refs — highest confidence
  nwr["ref:ekatte"](area.bg);

  // Resort complexes (курортни комплекси)
  nwr["leisure"="resort"](area.bg);
  nwr["tourism"="resort"](area.bg);

  // Industrial / business parks (бизнес паркове, индустриални зони)
  way["landuse"="industrial"]["name"](area.bg);
  relation["landuse"="industrial"]["name"](area.bg);

  // Quarters and neighbourhoods (квартали, вилни зони)
  nwr["place"~"^(quarter|neighbourhood|locality|suburb)$"](area.bg);

  // Generic boundary tagged as territorial formation
  nwr["boundary"="administrative"]["admin_level"~"^(9|10|11)$"](area.bg);
);
out body geom;
""".strip()


def run(timeout: int = 600, attempts: int = 5) -> dict:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    delay = 5.0
    for i in range(1, attempts + 1):
        print(f"POST {OVERPASS_URL} (attempt {i}/{attempts})", file=sys.stderr)
        try:
            r = requests.post(
                OVERPASS_URL,
                data={"data": QUERY},
                headers=headers,
                timeout=timeout,
            )
            if r.status_code in (429, 504) or 500 <= r.status_code < 600:
                ra = r.headers.get("Retry-After")
                wait = float(ra) if ra and ra.isdigit() else delay
                print(
                    f"  HTTP {r.status_code}; sleeping {wait:.0f}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
                delay *= 2
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if i == attempts:
                raise
            print(f"  network error: {e}; sleeping {delay:.0f}s", file=sys.stderr)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("Overpass: exhausted retries")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--cached",
        action="store_true",
        help="Skip download if data/osm_raw.json already exists.",
    )
    args = p.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if args.cached and RAW_OSM.exists():
        print(f"using cached {RAW_OSM}", file=sys.stderr)
        return 0

    payload = run()
    with RAW_OSM.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    elements = payload.get("elements", [])
    print(f"wrote {RAW_OSM} ({len(elements)} elements)", file=sys.stderr)
    by_type: dict[str, int] = {}
    for el in elements:
        by_type[el.get("type", "?")] = by_type.get(el.get("type", "?"), 0) + 1
    print(f"  by type: {by_type}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
