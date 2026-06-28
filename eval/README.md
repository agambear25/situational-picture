# eval/ — the accuracy spine (Phase-1 gate)

`python -m eval.harness` is the **hard gate**. It must exit 0 before any live feed is
wired (`config/runtime.yaml live_feeds_enabled`). It runs **fully offline** — frozen
verdicts stand in for Ollama, in-memory fixtures stand in for Postgres — so it is
deterministic in CI with no model and no GPU.

## What it asserts (HARD = blocking)

| Check | Meaning |
|---|---|
| `no_silent_drop` (HARD) | every observation lands in exactly one event or the reject ledger |
| `replay_bit_identical` (HARD) | rebuilding from the log reproduces the identical event set |
| `event_recall >= 0.95` | true incidents recovered into a single event |
| `pairwise_recall >= 0.90` | same-incident obs pairs end up co-merged |
| `over_merge_rate == 0` | no must-not-merge pair was fused |
| `gray_band_fully_frozen` | 0 model calls / 0 degraded — fixtures and verdicts are in sync |
| `echo_test` | single-family echoes collapse to one Rumored event, confidence not inflated |
| `band_correctness == 1.0` | each recovered incident's confidence band matches expectation |

## Files

- `fixtures/incidents_v1.yaml` — the **authored** ground-truth spec (the only file you edit by hand).
- `build_fixtures.py` — expands the spec into the three generated files below. Re-run on any
  deliberate corpus or threshold change.
- `fixtures/synthetic_v1.yaml` — generated fusion input (observations with real MGRS cells).
- `fixtures/ground_truth_v1.yaml` — generated answer key (partition, expectations, must-not-merge, echoes).
- `fixtures/verdicts_v1.json` — generated **frozen gray-band verdicts**, answered from ground
  truth. This is what lets CI replay the gray band with no Ollama.
- `metrics.py` — pure metric functions (the four failure modes: Drop, Over-merge, Fragment, Mis-confidence).
- `harness.py` — the runnable gate.
- `tests/test_harness.py` — the gate as pytest + the chaos test (Ollama-down ⇒ fragmentation, never a drop).

## The 8 hard cases the corpus covers

1. same incident via 2 independent families → merge, confirmed/High
2. same incident echoed 3× by one family → merge, Rumored, `echo-only`
3. two distinct incidents ~1km apart same hour → must NOT merge
4. place-name-only report joins via `place_id` across cells
5. paraphrases (gray band → frozen same)
6. mis-typed but compatible (gray → frozen same)
7. stranded singleton → 1-member event, never dropped
8. gray-band ambiguous → frozen **different** → stay separate

## Honest caveats

- **"Bit-identical" replay** holds against the **frozen** verdict + embedding caches. A cold
  re-query of a live Ollama is not guaranteed identical — which is exactly why CI always
  replays against `verdicts_v1.json`.
- **Corpus size.** v1 is a compact ~18-obs / 11-incident corpus chosen to exercise every hard
  case clearly, not the full ~200-obs scale. It can be grown (the generator scales); the
  Phase-2 labelling UI (`ui/components/label_studio.py`) is the intended way to add real
  hand-labelled incidents to `realworld_ua_v1.yaml`.
- **Model-free gray band is wide.** Without embeddings the lexical text signal is weak, so most
  same-incident pairs land in the gray band and are carried by frozen verdicts. With production
  384-d embeddings the text factor rises and the gray band shrinks toward the ≤15% target.
  The gate validates the *machinery* (blocking recall, no-drop, noisy-OR, bands, replay), not
  embedding quality — that is what the advisory `realworld_ua_v1.yaml` set is for.

## Regenerating fixtures

```bash
python -m eval.build_fixtures   # rewrites synthetic_v1 / ground_truth_v1 / verdicts_v1
python -m eval.harness          # must exit 0
```

Bumping any pinned version (embedding/model/prompt/schema) changes the cache keys, so the
frozen verdicts must be regenerated and the diff reviewed.
