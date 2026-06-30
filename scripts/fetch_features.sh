#!/usr/bin/env bash
# Download the Geofabrik Ukraine extract (CC-BY/ODbL) + load the Donbas feature library.
# Populates geo.geo_feature (water/roads/forests/builtup/rail) for the named-entity layer.
#
#   bash scripts/fetch_features.sh
set -e
OUT="${1:-data/ground_truth/osm}"
VPY="${VPY:-.venv/bin/python}"
PBF="$OUT/ukraine-latest.osm.pbf"
mkdir -p "$OUT"
if [ ! -s "$PBF" ]; then
  echo "downloading Geofabrik Ukraine extract (~870 MB) ..."
  curl -L --max-time 1800 "https://download.geofabrik.de/europe/ukraine-latest.osm.pbf" -o "$PBF"
fi
echo "loading features for ua_donbas ..."
"$VPY" -m geo.feature_load --theater ua_donbas --pbf "$PBF"
