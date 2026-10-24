"""URL safety: SSRF protection and prompt-injection pattern detection."""
import ipaddress
import re
import socket
from urllib.parse import urlparse, urlunparse

from . import config


NOT_AN_ADDRESS = "That does not look like a website address. Please enter one like www.yourwebsite.com or https://yourwebsite.com."
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def _plausible_host(host: str) -> bool:
    """True for IP addresses and for names that could be real domains (letters, digits, hyphens, a dot and a proper ending)."""
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        pass
    try:
        ascii_host = host.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return False
    labels = ascii_host.split(".")
    return len(labels) >= 2 and len(ascii_host) <= 253 and all(_LABEL.match(l) for l in labels) and (labels[-1].startswith("xn--") or labels[-1].isalpha() and len(labels[-1]) >= 2)


class UnsafeURL(ValueError):
    """Raised when a URL must not be fetched. The message is safe to show users."""


def is_demo_url(url: str) -> bool:
    """True only for the app's own bundled demo sites (http://127.0.0.1:PORT/demo/...)."""
    try:
        p = urlparse(url)
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError:
        return False
    return (
        p.scheme in ("http", "https")
        and (p.hostname or "") in ("127.0.0.1", "localhost")
        and port == config.APP_PORT
        and p.path.startswith("/demo/")
        and ".." not in p.path.split("/")
    )


def _check_ip(raw: str) -> None:
    ip = ipaddress.ip_address(raw.split("%")[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if not ip.is_global:
        raise UnsafeURL("This address points to a private, local or reserved network and cannot be audited.")


def validate_url(url: str) -> str:
    """Return a normalized URL or raise UnsafeURL. Called on the input AND on every redirect hop."""
    url = (url or "").strip()
    if not url or len(url) > 2048:
        raise UnsafeURL("Please enter a valid website URL.")
    if "://" not in url:
        url = "https://" + url
    try:
        p = urlparse(url)
        url = urlunparse(p._replace(scheme=p.scheme.lower(), netloc=p.netloc.lower()))  # HTTP://Example.COM/Page -> http://example.com/Page
        p = urlparse(url)
        port = p.port
    except ValueError:
        raise UnsafeURL(NOT_AN_ADDRESS)
    if p.scheme not in ("http", "https"):
        raise UnsafeURL("Only http and https URLs can be audited.")
    if not p.hostname:
        raise UnsafeURL(NOT_AN_ADDRESS)
    if p.username or p.password:
        raise UnsafeURL("URLs containing credentials are not allowed.")
    if is_demo_url(url):
        return url
    if port not in (None, 80, 443):
        raise UnsafeURL("Only standard web ports (80/443) are allowed.")
    if p.hostname in ("localhost",) or p.hostname.endswith((".local", ".internal", ".localhost")):
        raise UnsafeURL("This address points to a private, local or reserved network and cannot be audited.")
    if not _plausible_host(p.hostname):
        raise UnsafeURL(NOT_AN_ADDRESS)
    try:
        infos = socket.getaddrinfo(p.hostname, port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UnsafeURL("We could not find a website at that address. Please check it for typos, for example in the domain name.")
    for info in infos:
        _check_ip(info[4][0])
    return url


# Phrases that address an AI system instead of a human reader.
INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|prompts|rules|messages)",
    r"disregard (all |any |the )?(previous|prior|above|earlier|your) (instructions|prompts|rules)",
    r"forget (all |any )?(previous|prior|your) (instructions|rules)",
    r"you are now (a|an|the)\b",
    r"(reveal|print|show|output|repeat) (your |the )?(system|hidden|initial) (prompt|instructions)",
    r"do not (report|mention|flag|list) (any )?(issues|problems|errors|findings)",
    r"(report|state|say|declare) that (this|the) (site|website) (has no|is perfect|is fully)",
    r"(give|assign|rate) (this|the) (site|website) (a )?(perfect|100|maximum|top)",
    r"\bnew instructions?:",
    r"\bsystem (override|prompt|message):",
    r"as an ai (assistant|language model|model)[, ]",
]
_INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.I)


def find_injection(text: str, limit: int = 3) -> list[str]:
    """Return short, non-overlapping snippets (cut at word boundaries) of text that look like instructions aimed at an AI."""
    text = text or ""
    hits, last_end = [], -1
    for m in _INJECTION_RE.finditer(text):
        if m.start() < last_end:  # already inside the previous snippet
            continue
        start, end = max(0, m.start() - 20), min(len(text), m.end() + 120)
        snippet = text[start:end]
        if start > 0 and " " in snippet:
            snippet = snippet.split(" ", 1)[1]  # drop a partly cut first word
        if end < len(text) and " " in snippet:
            snippet = snippet.rsplit(" ", 1)[0]  # drop a partly cut last word
        hits.append(re.sub(r"\s+", " ", snippet).strip())
        last_end = end
        if len(hits) >= limit:
            break
    return hits


def redact_injection(text: str) -> str:
    """Replace every line that contains instruction-like text with a placeholder before text reaches a model.
    The raw text is still scanned (and reported) by code; the model simply never sees the payload."""
    out = []
    for line in (text or "").split("\n"):
        out.append("[line removed: it contained instructions aimed at AI systems]" if _INJECTION_RE.search(line) else line)
    return "\n".join(out)
