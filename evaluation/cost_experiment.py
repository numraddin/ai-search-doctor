"""Cost-reduction experiment: tokens saved vs detection lost.

Configurations (same sites, same ground truth, fresh independent runs each):
  full        page text up to 5000 chars per page, fix writer on   (production default)
  trunc400    page text cut to 400 chars per page, fix writer on
  trunc200    page text cut to 200 chars per page, fix writer on
  nofix       full page text, fix writer skipped
  trunc400+nofix  both reductions

Usage (app must be running):  python evaluation/cost_experiment.py --runs 3
Writes evaluation/cost_experiment.md and .json
"""
import argparse
import json
import statistics as st
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config  # noqa: E402
import evaluate as ev  # noqa: E402

CONFIGS = {
    "full": dict(PAGE_CHAR_BUDGET=5000, FIX_TOP_N=5),
    "trunc400": dict(PAGE_CHAR_BUDGET=400, FIX_TOP_N=5),
    "trunc200": dict(PAGE_CHAR_BUDGET=200, FIX_TOP_N=5),
    "nofix": dict(PAGE_CHAR_BUDGET=5000, FIX_TOP_N=0),
    "trunc400+nofix": dict(PAGE_CHAR_BUDGET=400, FIX_TOP_N=0),
}
SITE_KEYS = ["a", "b", "c", "d"]  # clean A tests false positives; B, C, D carry planted issues


def run_site(site, runs, tag):
    out = []
    for i in range(runs):
        r = ev.run_once(site, f"{tag}-{site}-{i}-{time.time_ns()}")
        out.append(ev.score_run(site, r) | {"n_fixes": sum(1 for f in r["findings"] if f.get("fix"))})
    return site, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()
    if not ev.server_up():
        sys.exit("Start the app first (python run.py).")
    results = {}
    for name, cfg in CONFIGS.items():
        for k, v in cfg.items():
            setattr(config, k, v)
        print(f"config {name} {cfg}", flush=True)
        with ThreadPoolExecutor(max_workers=len(SITE_KEYS)) as ex:
            results[name] = dict(ex.map(lambda s: run_site(s, args.runs, name), SITE_KEYS))
    for k, v in CONFIGS["full"].items():
        setattr(config, k, v)

    rows, base = [], None
    for name, per_site in results.items():
        runs = [r for s in per_site.values() for r in s]
        toks = [r["usage"]["total"]["total_tokens"] for r in runs]
        inp = [r["usage"]["total"]["input"] + r["usage"]["total"]["cache_read"] for r in runs]
        out = [r["usage"]["total"]["output"] for r in runs]
        cost = [r["usage"]["total"]["cost_usd"] for r in runs]
        hits = items = 0
        miss = {}
        for site in ("b", "c", "d"):
            for r in per_site[site]:
                for k, v in r["expected_hits"].items():
                    items += 1
                    hits += v
                    if not v:
                        miss[f"{site}:{k}"] = miss.get(f"{site}:{k}", 0) + 1
        unexpected = sum(len(r["unexpected"]) for s in per_site.values() for r in s)
        row = dict(config=name, tokens=st.mean(toks), input=st.mean(inp), output=st.mean(out), cost=st.mean(cost),
                   recall=hits / items, hits=hits, items=items, missed=miss, unexpected=unexpected,
                   fixes=st.mean(r["n_fixes"] for s in per_site.values() for r in s),
                   seconds=st.mean(r["seconds"] for s in per_site.values() for r in s))
        if name == "full":
            base = row
        rows.append(row)

    md = ["# Cost-reduction experiment", "",
          f"Model {config.AI_MODEL}; {args.runs} independent runs per site per configuration; sites A (clean), B, C, D. "
          f"Recall counts planted issues on B, C and D ({rows[0]['items']} checks per configuration). Cost uses OpenAI list prices "
          f"${config.PRICE_INPUT_PER_M}/M input and ${config.PRICE_OUTPUT_PER_M}/M output.", "",
          "| Configuration | Tokens/audit | Tokens saved | Cost/audit | Cost saved | Recall | Recall lost (points) | Unexpected findings | Fixes written/audit |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['config']} | {r['tokens']:.0f} | {1 - r['tokens'] / base['tokens']:.0%} | ${r['cost']:.5f} | {1 - r['cost'] / base['cost']:.0%} | "
                  f"{r['recall']:.0%} ({r['hits']}/{r['items']}) | {100 * (base['recall'] - r['recall']):.1f} | {r['unexpected']} | {r['fixes']:.1f} |")
    md += ["", "## Missed planted issues by configuration (count of runs missed)", ""]
    for r in rows:
        md.append(f"- **{r['config']}**: " + (", ".join(f"{k} ({v})" for k, v in sorted(r["missed"].items())) or "none"))
    md += ["", "Notes: truncation only matters when a page is longer than the budget. The test pages are short (about 400 to 900 characters of text), "
           "so 400 and 200 are aggressive cuts relative to them; real pages are usually much longer, where a 5000-character budget already truncates. "
           "Fix-writer quality is not scored here, only whether fixes were produced."]
    (ROOT / "evaluation" / "cost_experiment.md").write_text("\n".join(md) + "\n")
    (ROOT / "evaluation" / "cost_experiment.json").write_text(json.dumps(rows, indent=1, default=str))
    print("\n".join(md))


if __name__ == "__main__":
    main()
