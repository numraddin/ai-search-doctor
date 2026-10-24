"""PageSpeed parsing, optional behavior and score blending (the live Google call needs a key, so it is not exercised here)."""
from app import config, pagespeed, reporter
from app.findings import make_finding

SAMPLE = {"lighthouseResult": {"categories": {"performance": {"score": 0.42}}, "audits": {
    "largest-contentful-paint": {"numericValue": 5200.0}, "first-contentful-paint": {"numericValue": 2100.0},
    "total-blocking-time": {"numericValue": 640.0}, "cumulative-layout-shift": {"numericValue": 0.18}, "speed-index": {"numericValue": 4300.0},
    "unused-javascript": {"title": "Reduce unused JavaScript", "details": {"type": "opportunity", "overallSavingsMs": 1350}},
    "tiny": {"title": "Small thing", "details": {"type": "opportunity", "overallSavingsMs": 50}}}},
    "loadingExperience": {"overall_category": "SLOW"}}


def test_parse_extracts_metrics_and_top_opportunities():
    r = pagespeed.parse(SAMPLE)
    assert r["score"] == 42 and r["lcp_ms"] == 5200 and r["field_category"] == "SLOW"
    assert r["opportunities"] == [{"title": "Reduce unused JavaScript", "saves_s": 1.4}]


def test_disabled_without_key(monkeypatch):
    monkeypatch.setattr(config, "PAGESPEED_API_KEY", "")
    assert pagespeed.run([{"ok": True, "url": "https://example.com/", "rel": "/"}]) == {"enabled": False, "pages": {}, "average": None}


def test_lighthouse_is_blended_into_speed_score_without_double_counting():
    unscored = make_finding(dimension="speed_mobile", issue_type="lighthouse_mobile", title="t", severity="high", url="u", rel="/",
                            evidence=[], why="", recommendation="", source="code")
    unscored["scored"] = False
    facts, qs = [{"found": True}] * 6, [{"answered": True}] * 8
    s = reporter.run([unscored], facts, qs, True, lighthouse=40)["score"]["dimensions"]["speed_mobile"]["score"]
    assert s == 58  # 0.7 * 40 + 0.3 * 100
    assert reporter.run([], facts, qs, True)["score"]["dimensions"]["speed_mobile"]["score"] == 100
