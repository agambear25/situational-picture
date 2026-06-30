"""
Filter raw UCDP GED downloads → one combined Donbas CSV for ingest.ucdp_history.

Reads the definitive GED (v25.1, 2022–2024) plus every GED Candidate monthly file (2025–2026)
under <dir>/cand/, keeps Ukraine events inside the Donbas AOI bbox from 2022-02-24 on, de-dups by
UCDP id, and writes <dir>/ucdp_donbas.csv. Pure stdlib. See scripts/fetch_ucdp_donbas.sh.

    python scripts/filter_ucdp.py data/ground_truth/ucdp
"""
import csv
import glob
import sys
from collections import Counter

BBOX = (36.0, 46.8, 39.5, 49.5)   # ua_donbas: lon[min,max], lat[min,max] — keep in sync with theaters.yaml


def main(d: str) -> None:
    ged = sorted(glob.glob(f"{d}/GEDEvent_v*.csv"))            # definitive (covers 2022–2024)
    cands = sorted(glob.glob(f"{d}/cand/*.csv"))               # candidate monthlies (2025–2026)
    seen, rows, header = set(), [], None
    for path in ged + cands:
        with open(path, newline="", encoding="utf-8") as f:
            rd = csv.DictReader(f)
            header = rd.fieldnames
            for r in rd:
                if r.get("country") != "Ukraine":
                    continue
                ds = (r.get("date_start") or "")[:10]
                if len(ds) != 10 or ds < "2022-02-24":
                    continue
                try:
                    lon, lat = float(r["longitude"]), float(r["latitude"])
                except (KeyError, ValueError, TypeError):
                    continue
                if not (BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]):
                    continue
                uid = r.get("id")
                if uid in seen:
                    continue
                seen.add(uid)
                rows.append(r)
    rows.sort(key=lambda r: (r.get("date_start") or ""))
    out = f"{d}/ucdp_donbas.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)
    months = sorted({(r.get("date_start") or "")[:7] for r in rows})
    print(f"wrote {len(rows)} events → {out}")
    print(f"months: {len(months)}  ({months[0]} → {months[-1]})" if months else "no rows")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/ground_truth/ucdp")
