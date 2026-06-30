#!/usr/bin/env bash
# Download geoBoundaries admin boundaries (oblast/raion/hromada) for the admin substrate.
# Source: geoBoundaries gbOpen UKR ADM1/ADM2/ADM3, CC-BY 4.0, no auth. Then load with geo.admin_load.
#
#   bash scripts/fetch_admin.sh
#   python -m geo.admin_load --theater ua_donbas --dir data/ground_truth/admin
set -e
OUT="${1:-data/ground_truth/admin}"
VPY="${VPY:-.venv/bin/python}"
mkdir -p "$OUT"
for lvl in ADM1 ADM2 ADM3; do
  url=$(curl -s -L --max-time 30 "https://www.geoboundaries.org/api/current/gbOpen/UKR/${lvl}/" \
        | "$VPY" -c "import sys,json;print(json.load(sys.stdin)['gjDownloadURL'])")
  echo "downloading $lvl ($url) ..."
  curl -s -L --max-time 300 "$url" -o "$OUT/UKR_${lvl}.geojson"
  echo "  $lvl: $(wc -c < "$OUT/UKR_${lvl}.geojson") bytes"
done
echo "Done → $OUT. Next: $VPY -m geo.admin_load --theater ua_donbas --dir $OUT"
