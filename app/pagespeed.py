"""Optional real mobile speed test through Google PageSpeed Insights (Lighthouse).

Only runs when PAGESPEED_API_KEY is set (free key from Google Cloud). Without a key the app uses its own
file-size measurements. The key is only sent to Google and is never logged, stored or shown; error messages
never include the request address.
"""
from concurrent.futures import ThreadPoolExecutor

import httpx

from . import config
from .safety import is_demo_url

API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def parse(data: dict) -> dict:
    """Pull the numbers we show out of a PageSpeed Insights response."""
    lh = data["lighthouseResult"]
    a = lh["audits"]
    score = lh["categories"]["performance"]["score"]

    def val(k):
        return a.get(k, {}).get("numericValue")

    opps = []
    for k, v in a.items():
        d = v.get("details") or {}
        if d.get("type") == "opportunity" and (d.get("overallSavingsMs") or 0) > 300:
            opps.append((d["overallSavingsMs"], v.get("title", k)))
    opps.sort(reverse=True)
    field = (data.get("loadingExperience") or {}).get("overall_category")
    return {"score": None if score is None else round(score * 100), "lcp_ms": val("largest-contentful-paint"), "fcp_ms": val("first-contentful-paint"),
            "tbt_ms": val("total-blocking-time"), "cls": val("cumulative-layout-shift"), "speed_index_ms": val("speed-index"),
            "opportunities": [{"title": t, "saves_s": round(ms / 1000, 1)} for ms, t in opps[:3]], "field_category": field}


def _one(url: str) -> dict:
    try:
        r = httpx.get(API, params={"url": url, "strategy": "mobile", "category": "performance", "key": config.PAGESPEED_API_KEY},
                      timeout=75.0, trust_env=False)
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"Google PageSpeed could not be reached ({type(e).__name__})."}
    if r.status_code == 429:
        return {"ok": False, "error": "Google PageSpeed quota reached."}
    if r.status_code in (400, 403):
        return {"ok": False, "error": "Google PageSpeed refused the request (check the key or that the page is public)."}
    if r.status_code != 200:
        return {"ok": False, "error": f"Google PageSpeed returned an error ({r.status_code})."}
    try:
        return {"ok": True, **parse(r.json())}
    except (KeyError, ValueError, TypeError):
        return {"ok": False, "error": "Google PageSpeed returned an unreadable result."}


def run(pages: list[dict]) -> dict:
    """Test each readable page (public addresses only). Returns {"enabled", "pages": {rel: result}, "average"}."""
    if not config.PAGESPEED_API_KEY:
        return {"enabled": False, "pages": {}, "average": None}
    todo = [p for p in pages if p.get("ok") and not is_demo_url(p["url"])]
    with ThreadPoolExecutor(max_workers=3) as ex:
        results = list(ex.map(lambda p: _one(p["url"]), todo))
    out = {p["rel"]: r for p, r in zip(todo, results)}
    scores = [r["score"] for r in out.values() if r.get("ok") and r.get("score") is not None]
    return {"enabled": True, "pages": out, "average": round(sum(scores) / len(scores)) if scores else None,
            "skipped_local": len(pages) - len(todo)}
