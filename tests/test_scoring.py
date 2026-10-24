"""Scoring must be strict: failures on the basics cannot be averaged away."""
from app import reporter
from app.findings import make_finding


def f(issue_type, dim, sev, status="confirmed"):
    return make_finding(dimension=dim, issue_type=issue_type, title=issue_type, severity=sev, url="u", rel="/", evidence=[], why="", recommendation="", source="code", status=status)


FACTS = [{"found": True}] * 6
QS = [{"answered": True}] * 8


def test_nothing_read_is_not_scored():
    r = reporter.run([f("fetch_failed", "readable", "high")], [], [], False, pages_read=0, pages_requested=1)["score"]
    assert r["overall"] is None and r["not_scored"]
    assert all(d["score"] is None for d in r["dimensions"].values())


def test_missing_title_caps_overall_at_60():
    r = reporter.run([f("missing_title", "readable", "high")], FACTS, QS, True)["score"]
    assert r["overall"] <= 60 and r["caps"]


def test_other_high_issue_caps_at_75():
    r = reporter.run([f("price_contradiction", "credibility", "high")], FACTS, QS, True)["score"]
    assert r["overall"] <= 75


def test_weakest_link_limits_the_average():
    fs = [f("a", "readable", "high"), f("b", "readable", "high"), f("c", "readable", "high"), f("d", "readable", "high")]  # readable -> 0
    r = reporter.run(fs, FACTS, QS, True)["score"]
    assert r["dimensions"]["readable"]["score"] == 0 and r["overall"] <= 35


def test_clean_site_scores_high_and_potential_counts_half():
    assert reporter.run([], FACTS, QS, True)["score"]["overall"] == 100
    full = reporter.run([f("x", "credibility", "medium")], FACTS, QS, True)["score"]["dimensions"]["credibility"]["score"]
    half = reporter.run([f("x", "credibility", "medium", "potential")], FACTS, QS, True)["score"]["dimensions"]["credibility"]["score"]
    assert full == 88 and half == 94


def test_ai_not_run_leaves_answer_quality_not_measured():
    r = reporter.run([], [], [], False)["score"]
    assert r["dimensions"]["answer_quality"]["score"] is None and r["partial"]


def test_firewall_refusal_is_explained_not_just_unscored():
    refused = f("access_refused", "readable", "high")
    r = reporter.run([refused], [], [], False, pages_read=0, pages_requested=1)["score"]
    assert r["not_scored"] and "firewall" in r["reason"]
