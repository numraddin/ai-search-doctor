"""Domain credibility lookups using free public sources that need no API key.

  * RDAP (the public registry lookup that replaced WHOIS): when the domain was first registered.
  * Tranco list: a research-grade ranking of the most visited domains (context only, never scored).
  * DNS via Google DNS-over-HTTPS: whether SPF and DMARC email-authentication records exist.

Every lookup has a short timeout and fails soft: if a source is unreachable the result says "not checked"
and nothing is penalized. Only the domain name is sent to these services (never page content).
"""
import ipaddress
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import httpx

UA = "AISearchDoctor/1.0 (domain credibility lookup)"
TWO_LEVEL = {"co", "com", "org", "net", "gov", "edu", "ac"}


def registrable(host: str) -> str:
    """Best-effort registrable domain (www.example.co.uk -> example.co.uk)."""
    parts = host.lower().removeprefix("www.").split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in TWO_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _get(url: str):
    return httpx.get(url, timeout=5.0, follow_redirects=True, headers={"User-Agent": UA, "Accept": "application/json"}, trust_env=False)


def _age(domain: str) -> dict:
    try:
        r = _get(f"https://rdap.org/domain/{domain}")
        r.raise_for_status()
        for e in r.json().get("events", []):
            if e.get("eventAction") == "registration":
                d = date.fromisoformat(e["eventDate"][:10])
                return {"registered": d.isoformat(), "age_days": (date.today() - d).days}
        return {"registered": None, "age_days": None, "age_error": "no registration date published"}
    except Exception as e:
        return {"registered": None, "age_days": None, "age_error": type(e).__name__}


def _rank(domain: str) -> dict:
    try:
        r = _get(f"https://tranco-list.eu/api/ranks/domain/{domain}")
        r.raise_for_status()
        ranks = r.json().get("ranks") or []
        return {"tranco_checked": True, "tranco_rank": ranks[0]["rank"] if ranks else None}
    except Exception:
        return {"tranco_checked": False, "tranco_rank": None}


def _txt(name: str):
    """TXT record strings for `name`, [] if none, None if the lookup failed."""
    try:
        r = _get(f"https://dns.google/resolve?name={name}&type=TXT")
        r.raise_for_status()
        return [a.get("data", "").strip('"') for a in r.json().get("Answer", []) if a.get("type") == 16]
    except Exception:
        return None


def _mail(domain: str) -> dict:
    spf, dmarc = _txt(domain), _txt(f"_dmarc.{domain}")
    pol = None
    if dmarc:
        m = re.search(r"\bp=(\w+)", " ".join(dmarc))
        pol = m.group(1).lower() if m else None
    return {"spf": None if spf is None else any(t.lower().startswith("v=spf1") for t in spf),
            "dmarc": None if dmarc is None else any(t.lower().startswith("v=dmarc1") for t in dmarc), "dmarc_policy": pol}


def lookup(host: str) -> dict:
    host = (host or "").lower()
    try:
        ipaddress.ip_address(host.strip("[]"))
        is_ip = True
    except ValueError:
        is_ip = False
    if not host or is_ip or host == "localhost" or "." not in host:
        return {"skipped": True, "reason": "Domain checks are skipped for local test sites and IP addresses."}
    domain = registrable(host)
    with ThreadPoolExecutor(max_workers=3) as ex:
        a, b, c = ex.submit(_age, domain), ex.submit(_rank, domain), ex.submit(_mail, domain)
        out = {"skipped": False, "domain": domain, **a.result(), **b.result(), **c.result()}
    return out
