#!/usr/bin/env bash
# Download ESA WorldCover 10m tiles + the Geofabrik Ukraine OSM extract for the geography substrate.
# Data → data/ground_truth/{worldcover,osm}/ (gitignored). Reproducible; skips files already present.
set -euo pipefail
WC_DIR="data/ground_truth/worldcover"; OSM_DIR="data/ground_truth/osm"
mkdir -p "$WC_DIR" "$OSM_DIR"
BASE="https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
# ua_donbas + black_sea tiles (WorldCover 3° SW-corner names, multiples of 3). N45E036 is shared.
TILES="N45E036 N45E039 N48E036 N48E039 N42E033 N42E036 N45E033"
for t in $TILES; do
  f="$WC_DIR/ESA_WorldCover_10m_2021_v200_${t}_Map.tif"
  if [ -s "$f" ]; then echo "have $t"; continue; fi
  echo "fetch $t"; curl -fsSL "$BASE/ESA_WorldCover_10m_2021_v200_${t}_Map.tif" -o "$f" \
    || { echo "  (tile $t absent — likely all-ocean, skipping)"; rm -f "$f"; }
done
PBF="$OSM_DIR/ukraine-latest.osm.pbf"
[ -s "$PBF" ] || curl -fSL "https://download.geofabrik.de/europe/ukraine-latest.osm.pbf" -o "$PBF"
echo "geography data ready in $WC_DIR + $OSM_DIR"
