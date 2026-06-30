#!/usr/bin/env bash
# Fetch + filter UCDP GED conflict data for the Donbas AOI → a combined CSV for ingest.ucdp_history.
# Sources are CC-BY 4.0, no auth: definitive GED v25.1 (1989–2024) + GED Candidate monthlies (2025+).
# UCDP Candidate files are PER-MONTH (vYY_0_M = month M of 20YY), so we pull them all.
#
#   bash scripts/fetch_ucdp_donbas.sh
#   UCDP_GED_FILE=data/ground_truth/ucdp/ucdp_donbas.csv python -m ingest.ucdp_history --theater ua_donbas
#   python -m fusion.run --theater ua_donbas && python -m assess.run --theater ua_donbas
set -e
OUT="${1:-data/ground_truth/ucdp}"
BASE="https://ucdp.uu.se/downloads"
mkdir -p "$OUT/cand"

echo "[1/3] definitive GED v25.1 (2022–2024) ..."
curl -sL "$BASE/ged/ged251-csv.zip" -o "$OUT/ged251.zip"
unzip -o "$OUT/ged251.zip" -d "$OUT" >/dev/null

echo "[2/3] GED Candidate monthlies (2025–2026; 404s = not yet released) ..."
for v in 25 26; do for m in $(seq 1 12); do
  f="$OUT/cand/c_${v}_${m}.csv"
  code=$(curl -sL --max-time 30 -o "$f" -w '%{http_code}' "$BASE/candidateged/GEDEvent_v${v}_0_${m}.csv")
  if [ "$code" = "200" ] && head -1 "$f" 2>/dev/null | grep -q "^id,relid"; then echo "  ok v${v}_0_${m}"; else rm -f "$f"; fi
done; done

echo "[3/3] filter → Donbas combined CSV ..."
python scripts/filter_ucdp.py "$OUT"
echo "Done. Next: UCDP_GED_FILE=$OUT/ucdp_donbas.csv python -m ingest.ucdp_history --theater ua_donbas"
