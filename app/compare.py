"""Before/after comparison of two audits of the same website."""
import re

SITE_LEVEL = {"no_sitemap", "ai_bots_blocked", "robots_blocks_all", "missing_structured_data", "duplicate_title", "blocked_page"}


def _words(s):
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _similar(a, b):
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / max(1, len(wa | wb))


def _slim(f):
    return {k: f[k] for k in ("key", "issue_type", "dimension", "title", "severity", "page", "url", "status", "evidence", "recommendation")}


def _caveat(delta: int) -> str:
    tail = "Score changes show that detected problems changed. They are not evidence of better or worse visibility in AI search results."
    if delta > 0:
        return "The score went up: some detected problems were fixed. " + tail
    if delta < 0:
        return "The score went down: new problems appeared or existing ones got worse. " + tail
    return "The score did not change. " + tail


def compare(old: dict, new: dict) -> dict:
    if old["score"]["overall"] is None or new["score"]["overall"] is None:
        raise ValueError("One of these audits has no score because the website could not be read, so it cannot be compared.")
    if old.get("id") == new.get("id"):
        raise ValueError("Please pick two different audits to compare.")
    if old.get("site", old.get("domain")) != new.get("site", new.get("domain")):
        raise ValueError("Please compare two audits of the same website.")
    old_f, new_f = list(old["findings"]), list(new["findings"])
    matched, unmatched_new, pool = [], [], list(old_f)

    # pass 1: same key, or same type and page with a similar title
    for n in new_f:
        best, best_s = None, 0.0
        for o in pool:
            if o["key"] == n["key"]:
                best, best_s = o, 2.0
                break
            if o["issue_type"] == n["issue_type"] and o["page"] == n["page"]:
                s = _similar(o["title"], n["title"])
                if s >= 0.5 and s > best_s:
                    best, best_s = o, s
        if best:
            pool.remove(best)
            matched.append((best, n))
        else:
            unmatched_new.append(n)
    # pass 2: the AI may reword the same problem, so pair what is left by topic (same type, same page)
    for n in list(unmatched_new):
        twin = next((o for o in pool if o["issue_type"] == n["issue_type"] and o["page"] == n["page"]), None)
        if twin:
            pool.remove(twin)
            unmatched_new.remove(n)
            matched.append((twin, n))

    # pass 3: same check area and page with fairly similar wording (the AI may even label the type differently)
    for n in list(unmatched_new):
        cands = [(_similar(o["title"], n["title"]), o) for o in pool if o["dimension"] == n["dimension"] and o["page"] == n["page"]]
        score, twin = max(cands, key=lambda x: x[0], default=(0, None))
        if twin is not None and score >= 0.3:
            pool.remove(twin)
            unmatched_new.remove(n)
            matched.append((twin, n))
    new_pages = {p["rel"] for p in new["pages"] if p.get("ok")}
    resolved = []
    for o in pool:
        item = _slim(o)
        item["recheck_confirmed"] = o["page"] in new_pages or o["issue_type"] in SITE_LEVEL
        item["now"] = ("No longer detected in the new audit." if item["recheck_confirmed"]
                       else "No longer detected, but this page was not re-crawled, so treat as unconfirmed.")
        resolved.append(item)

    dims = {}
    for d, v in new["score"]["dimensions"].items():
        before = old["score"]["dimensions"].get(d, {}).get("score")
        dims[d] = {"label": v["label"], "before": before, "after": v["score"],
                   "delta": None if (before is None or v["score"] is None) else v["score"] - before}
    delta = new["score"]["overall"] - old["score"]["overall"]
    return {
        "old": {"id": old["id"], "url": old["url"], "timestamp": old["timestamp"], "score": old["score"]["overall"], "issues": len(old_f)},
        "new": {"id": new["id"], "url": new["url"], "timestamp": new["timestamp"], "score": new["score"]["overall"], "issues": len(new_f)},
        "score_delta": delta,
        "dimensions": dims,
        "resolved": resolved,
        "remaining": [_slim(n) for _, n in matched],
        "new_issues": [_slim(n) for n in unmatched_new],
        "caveat": _caveat(delta),
    }
