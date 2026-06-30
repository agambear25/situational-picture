"""
Synthesis runner (Phase 6a) — generate + cache the per-area Read for every area of interest.

Offline, like fusion.run / assess.run: reads the read model + assessments, calls the local LLM
(with the deterministic fallback), writes world.area_read. Run after assess.run:

    python -m synth.run --theater ua_donbas            # local LLM Reads
    python -m synth.run --theater ua_donbas --no-llm   # deterministic template Reads (fast, $0)
"""
from __future__ import annotations

import argparse
import logging
import os

logger = logging.getLogger(__name__)


def run(theater_id: str = "ua_donbas", use_llm: bool = True) -> dict:
    import psycopg2
    from api.queries import gather_area_context, list_aois, upsert_read
    from synth.llm import ollama_generate_fn
    from synth.service import synthesize_area

    gen = ollama_generate_fn() if use_llm else None
    conn = psycopg2.connect(os.environ.get("DB_DSN", "postgresql://localhost:5432/osint_cop"))
    n_llm = n_tmpl = 0
    try:
        aois = list_aois(conn, theater_id)
        for a in aois:
            ctx = gather_area_context(conn, a["aoi_id"])
            if not ctx:
                continue
            s = synthesize_area(ctx, generate_fn=gen)
            upsert_read(conn, a["aoi_id"], s["read"], s["input_hash"])
            if s["read"]["generated_by"] == "llm":
                n_llm += 1
            else:
                n_tmpl += 1
            logger.info("read %s: %s (%s)", a["label"], s["attention"]["status"], s["read"]["generated_by"])
        return {"theater_id": theater_id, "areas": len(aois), "llm": n_llm, "template": n_tmpl}
    finally:
        conn.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m synth.run")
    p.add_argument("--theater", default="ua_donbas")
    p.add_argument("--no-llm", action="store_true", help="deterministic template Reads only")
    args = p.parse_args()
    s = run(args.theater, use_llm=not args.no_llm)
    print("=" * 56)
    print(f"AREA READS — {s['theater_id']}")
    print(f"  areas          : {s['areas']}")
    print(f"  LLM reads      : {s['llm']}")
    print(f"  template reads : {s['template']}")
    print("=" * 56)


if __name__ == "__main__":
    main()
