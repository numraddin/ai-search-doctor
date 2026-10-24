"""Audit real public websites (no ground truth) and dump every finding with its evidence for MANUAL review.

Usage:  python evaluation/real_site.py "https://example.com,https://example.com/about" [more audits]
Each argument is ONE audit: up to 5 comma-separated links on the same website.
Writes evaluation/real_<domain>.md. Reviewer marks each finding correct / incorrect by hand.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import config, pipeline  # noqa: E402

config.DB_PATH = ROOT / "evaluation" / "eval.db"


def main(urls):
    for url in urls:
        job = pipeline.new_job([u.strip() for u in url.split(",")])
        pipeline.run_audit(job)
        if job["status"] != "done":
            print(f"{url}: {job['error']}")
            continue
        r = job["result"]
        t = r["usage"]["total"]
        lines = [f"# Real-site audit: {r['domain']}", "", f"Score {r['score']['overall']} (heuristic). Pages: {[p['rel'] for p in r['pages'] if p['ok']]}. "
                 f"Blocked: {[b['rel'] for b in r['blocked']]}. Tokens {t['total_tokens']}, cost ${t['cost_usd']:.5f}.",
                 f"Verifier: {r['verification']}", f"Warnings: {r['warnings']}", "", "Summary by the content agent: " + r["business_summary"], "",
                 "## Findings (review each: correct / incorrect)", ""]
        for f in r["findings"]:
            lines.append(f"- [ ] **{f['severity']}/{f['status']}/{f['source']}** `{f['issue_type']}` {f['page']}: {f['title']}")
            for e in f["evidence"][:2]:
                lines.append(f"    - evidence: {e['text'][:220]}")
        lines += ["", "## Rejected by verifier", ""] + [f"- `{x['issue_type']}` {x['title']}: {x['reason']}" for x in r["rejected"]]
        out = ROOT / "evaluation" / f"real_{r['domain'].replace(':', '_')}.md"
        out.write_text("\n".join(lines) + "\n")
        print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1:] or ["https://basecamp.com"])
