"""Agent 5: Prioritizer & Reporter. Deterministic, documented scoring so retests are stable.

Scoring is deliberately strict: a page that fails the basics must not look healthy just because other areas have
nothing to report. Three safeguards: heavier penalties, caps for critical failures, and "not scored / not measured"
instead of a default 100 when there is no evidence.
"""
from .content import FACT_KEYS
from .findings import DIMENSIONS

WEIGHTS = {"readable": 20, "answer_quality": 25, "credibility": 20, "speed_mobile": 10, "freshness": 10, "structured_data": 15}
PENALTY = {"high": 25, "medium": 12, "low": 4}
SEV_RANK = {"high": 0, "medium": 1, "low": 2}
EFFORT_RANK = {"low": 0, "medium": 1, "high": 2}
# Confirmed findings of these types mean AI systems cannot properly use the page at all.
CRITICAL_TYPES = {"missing_title", "robots_blocks_all", "js_rendered", "fetch_failed", "access_refused"}
CAP_CRITICAL, CAP_ANY_HIGH, CAP_MOSTLY_UNREAD = 60, 75, 50
WEAKEST_LINK_GAP = 35

METHODOLOGY = (
    "Each of the six areas starts at 100. Every verified issue subtracts points (high 25, medium 12, low 4; 'potential' issues count half). "
    "Answer quality blends the share of likely customer questions the pages answer (40%), the share of six key business facts clearly stated (30%) "
    "and the issue-based score (30%). The overall score is a weighted average (Answer quality 25, Can AI read the page 20, Credibility 20, "
    "Structured data 15, Speed & mobile 10, Freshness 10), with three strict rules: (1) the overall score can be at most 35 points above the weakest area; "
    "(2) it is capped at 60 if the page has no title, is JavaScript-only, is blocked entirely, or could not be fetched, and capped at 75 if any other "
    "confirmed high-priority issue exists; (3) if fewer than half of the pages you entered could be read it is capped at 50, and if none could be read "
    "the audit is 'Not scored'. An area with no evidence is shown as 'Not measured' and left out of the average. "
    "Credibility also uses public domain data (registration age, email protection). When a Google PageSpeed key is configured, Speed & mobile is 70% Google's Lighthouse mobile score and 30% our file-size checks; otherwise it comes from loading each page's real files (scripts, styles, images) and measuring size, request count and outside services. It is a strong proxy for mobile speed, but it is not Google Core Web Vitals or a test on a real phone."
)
DISCLAIMER = (
    "The AI referral score is an internal checklist score: how many of the known good-practice conditions for being found, trusted and cited by AI your pages meet. It is NOT a prediction, a search-engine ranking or a visibility metric, and a higher "
    "score does not guarantee better placement or traffic in ChatGPT, Claude or Gemini results. "
    "Off-site factors such as backlinks, brand mentions and reviews on other sites are not measured."
)


def run(findings: list[dict], facts: list[dict], questions: list[dict], llm_ran: bool, pages_read: int = 1, pages_requested: int = 1,
        lighthouse: int | None = None) -> dict:
    penalties = {d: 0.0 for d in WEIGHTS}
    for f in findings:
        if f.get("scored") is False:
            continue
        penalties[f["dimension"]] += PENALTY[f["severity"]] * (0.5 if f["status"] == "potential" else 1)
    nothing_read = pages_read == 0

    dims = {}
    for d, w in WEIGHTS.items():
        score, note = max(0.0, 100 - penalties[d]), ""
        if nothing_read:
            score, note = None, "Not measured: no page could be read"
        elif d == "answer_quality":
            if llm_ran and questions:
                answered = sum(q["answered"] for q in questions)
                found = sum(f["found"] for f in facts)
                score = 0.4 * (100 * answered / len(questions)) + 0.3 * (100 * found / len(FACT_KEYS)) + 0.3 * score
                note = f"{answered} of {len(questions)} likely customer questions answered; {found} of {len(FACT_KEYS)} key facts stated"
            else:
                score, note = None, "Not measured: the AI content analysis did not run"
        if d == "speed_mobile" and score is not None and lighthouse is not None:
            score = 0.7 * lighthouse + 0.3 * score
            note = f"Includes Google's real mobile speed test (Lighthouse {lighthouse}/100, weighted 70%)"
        dims[d] = {"label": DIMENSIONS[d], "score": None if score is None else round(score), "weight": w, "note": note,
                   "issues": sum(1 for f in findings if f["dimension"] == d)}

    measured = {d: v for d, v in dims.items() if v["score"] is not None}
    caps, overall, reason = [], None, ""
    if nothing_read:
        refused = any(f["issue_type"] == "access_refused" for f in findings)
        reason = ("The website's firewall refused our reader, so there was nothing to score. See the suggestion below to allow it, then analyze again." if refused
                  else "None of the pages you entered could be read, so there is nothing to score.")
    else:
        avg = sum(v["score"] * v["weight"] for v in measured.values()) / sum(v["weight"] for v in measured.values())
        overall = avg
        worst = min(v["score"] for v in measured.values())
        if overall > worst + WEAKEST_LINK_GAP:
            overall = worst + WEAKEST_LINK_GAP
            caps.append(f"Held to {round(overall)} because the weakest area is much lower than the rest.")
        critical = sorted({f["title"] for f in findings if f["issue_type"] in CRITICAL_TYPES and f["status"] == "confirmed"})
        high = [f for f in findings if f["severity"] == "high" and f["status"] == "confirmed"]
        if pages_requested and pages_read / pages_requested < 0.5 and overall > CAP_MOSTLY_UNREAD:
            overall = CAP_MOSTLY_UNREAD
            caps.append(f"Capped at {CAP_MOSTLY_UNREAD}: only {pages_read} of {pages_requested} pages you entered could be read.")
        if critical and overall > CAP_CRITICAL:
            overall = CAP_CRITICAL
            caps.append(f"Capped at {CAP_CRITICAL}: " + "; ".join(critical) + ".")
        elif high and overall > CAP_ANY_HIGH:
            overall = CAP_ANY_HIGH
            caps.append(f"Capped at {CAP_ANY_HIGH}: a high-priority issue is unresolved.")
        overall = round(overall)

    order = sorted(findings, key=lambda f: (SEV_RANK[f["severity"]], f["status"] != "confirmed", EFFORT_RANK.get(f["effort"], 1)))
    for i, f in enumerate(order, 1):
        f["rank"] = i
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in ("high", "medium", "low")}
    counts["confirmed"] = sum(1 for f in findings if f["status"] == "confirmed")
    counts["potential"] = sum(1 for f in findings if f["status"] == "potential")
    return {"findings": order, "counts": counts,
            "score": {"overall": overall, "not_scored": overall is None, "reason": reason, "caps": caps,
                      "partial": any(v["score"] is None for v in dims.values()) and not nothing_read,
                      "dimensions": dims, "methodology": METHODOLOGY, "disclaimer": DISCLAIMER, "ai_analysis_ran": llm_ran}}
