#!/usr/bin/env bash
# Orchestrate the territorial-formations pipeline end-to-end.
#
# Usage:
#   scripts/build.sh           # full refresh — re-downloads NSI and OSM
#   scripts/build.sh --cached  # reuse data/nsi_raw.json and data/osm_raw.json
#
# Idempotent: with unchanged upstream data, output GeoJSON/CSV is byte-identical.

set -euo pipefail

cd "$(dirname "$0")/.."

PY=${PYTHON:-python3}
ARGS=()
if [[ "${1:-}" == "--cached" ]]; then
  ARGS+=(--cached)
fi

echo "==> 1/3 NSI registry"
"$PY" scripts/fetch_nsi.py "${ARGS[@]}"

echo "==> 2/3 OSM polygons (Overpass)"
"$PY" scripts/fetch_osm.py "${ARGS[@]}"

echo "==> 3/3 matching + GeoJSON"
"$PY" scripts/match.py

echo "==> done"
