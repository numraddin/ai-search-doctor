"""Shared finding structure and vocabulary."""
import re

DIMENSIONS = {
    "readable": "Can AI read it?",
    "answer_quality": "Answer quality",
    "credibility": "Credibility",
    "speed_mobile": "Speed & mobile",
    "freshness": "Freshness",
    "structured_data": "Structured data",
}
SEVERITIES = ("high", "medium", "low")


def rel_path(root: str, url: str) -> str:
    """Page path relative to the audited site root, e.g. '/' or '/about.html'."""
    if url.startswith(root):
        return "/" + url[len(root):]
    return url


def slug(text: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:n]


def make_finding(*, dimension, issue_type, title, severity, url, rel, evidence, why, recommendation,
                 source, status="confirmed", confidence="high", key_suffix="", effort="medium"):
    """A finding. `evidence` is a list of {kind: 'quote'|'observation', url, text}."""
    return {
        "key": f"{issue_type}:{rel}" + (f":{key_suffix}" if key_suffix else ""),
        "issue_type": issue_type,
        "dimension": dimension,
        "title": title,
        "severity": severity,
        "url": url,
        "page": rel,
        "evidence": evidence,
        "why": why,
        "recommendation": recommendation,
        "status": status,  # confirmed | potential
        "confidence": confidence,
        "source": source,  # code | llm
        "effort": effort,
        "verification_note": "",
        "fix": None,
    }
