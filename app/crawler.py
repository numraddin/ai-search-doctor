"""Agent 1: Crawler & Access Agent.

Pure code, no LLM. Validates every request (including redirects), respects robots.txt,
enforces timeouts / size / page limits, and extracts structured page data. HTML is only
parsed, never executed.
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from . import config, dates as D, domain as DOM, pagespeed as PS
from .findings import rel_path
from .safety import UnsafeURL, find_injection, is_demo_url, validate_url

AI_BOTS = ["GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended", "CCBot"]
SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|svg|webp|ico|zip|css|js|json|xml|txt|mp4|mp3|woff2?|docx?|xlsx?)$", re.I)
PRIORITY_WORDS = [
    ("pricing", 6), ("price", 6), ("plans", 5), ("product", 5), ("service", 5), ("solution", 4),
    ("feature", 4), ("about", 5), ("faq", 5), ("contact", 4), ("shop", 3), ("team", 2), ("how", 2),
]


class FetchResult:
    def __init__(self, url, status=None, ctype="", body="", error=None, truncated=False, perf=None):
        self.url, self.status, self.ctype, self.body = url, status, ctype, body
        self.error, self.truncated = error, truncated
        self.perf = perf or {}


def fetch(url: str, accept=("text/html", "application/xhtml")) -> FetchResult:
    """Fetch one URL safely. Never raises; failures are returned in .error."""
    deadline = time.monotonic() + config.FETCH_DEADLINE
    current = url
    headers = {"User-Agent": config.USER_AGENT, "Accept": "text/html,application/xhtml+xml,text/plain,application/xml;q=0.8"}
    try:
        try:
            import h2  # noqa: F401  (HTTP/2 support; some sites reject plain HTTP/1.1 clients)
            use_h2 = True
        except ImportError:
            use_h2 = False
        with httpx.Client(timeout=httpx.Timeout(config.FETCH_TIMEOUT), follow_redirects=False,
                          headers=headers, trust_env=False, http2=use_h2) as client:
            for _ in range(config.MAX_REDIRECTS + 1):
                current = validate_url(current)  # re-validated on every hop
                t_req = time.monotonic()
                with client.stream("GET", current) as r:
                    ttfb = time.monotonic() - t_req
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        current = urljoin(current, r.headers["location"])
                        continue
                    ctype = r.headers.get("content-type", "").lower()
                    if r.status_code >= 400:
                        why = {401: "login required", 403: "access refused: the website blocks automated readers", 404: "page not found",
                               429: "too many requests", 503: "service unavailable"}.get(r.status_code, "")
                        return FetchResult(current, r.status_code, ctype, error=f"HTTP {r.status_code}" + (f" ({why})" if why else ""))
                    if ctype and not any(a in ctype for a in accept):
                        return FetchResult(current, r.status_code, ctype, error=f"Unsupported content type ({ctype.split(';')[0]})")
                    chunks, size, truncated = [], 0, False
                    for chunk in r.iter_bytes():
                        size += len(chunk)
                        chunks.append(chunk)
                        if size > config.MAX_BYTES:
                            truncated = True
                            break
                        if time.monotonic() > deadline:
                            return FetchResult(current, r.status_code, ctype, error="Timed out while downloading")
                    raw = b"".join(chunks)[: config.MAX_BYTES]
                    body = raw.decode(r.encoding or "utf-8", errors="replace")
                    perf = {"ttfb": round(ttfb, 3), "total": round(time.monotonic() - t_req, 3), "bytes": size,
                            "encoding": r.headers.get("content-encoding", "").lower(), "https": current.startswith("https://"),
                            "hsts": "strict-transport-security" in r.headers}
                    return FetchResult(current, r.status_code, ctype, body, truncated=truncated, perf=perf)
            return FetchResult(current, error="Too many redirects")
    except UnsafeURL as e:
        return FetchResult(current, error=f"Blocked for safety: {e}")
    except httpx.TimeoutException:
        return FetchResult(current, error="Request timed out")
    except httpx.HTTPError as e:
        return FetchResult(current, error=f"Connection failed ({type(e).__name__})")


def site_root(url: str) -> str:
    p = urlparse(url)
    if is_demo_url(url):
        parts = p.path.split("/")  # ['', 'demo', 'a', ...]
        if len(parts) > 2 and parts[2]:
            return f"{p.scheme}://{p.netloc}/demo/{parts[2]}/"
        return f"{p.scheme}://{p.netloc}/demo/"
    return f"{p.scheme}://{p.netloc}/"


def _normalize(url: str) -> str:
    url = url.split("#")[0].split("?")[0]
    if url.endswith("/index.html"):
        url = url[: -len("index.html")]
    return url


_JSONLD_KEYS = ("telephone", "email", "price", "streetAddress", "datePublished", "dateModified")
_DATE_META = {"article:published_time", "article:modified_time", "og:updated_time", "date", "last-modified", "datepublished",
              "datemodified", "dc.date", "dcterms.modified", "dcterms.created"}
_SOCIAL = re.compile(r"(linkedin\.com|facebook\.com|twitter\.com|//(?:www\.)?x\.com|instagram\.com|youtube\.com|tiktok\.com|pinterest\.com|github\.com)", re.I)
_REVIEWS = re.compile(r"\b(testimonials?|customer reviews?|client reviews?|reviews|rated\s+\d|\d(?:\.\d)?\s*(?:out of 5|/\s*5|stars))\b", re.I)
_ARTICLE_TYPES = {"Article", "BlogPosting", "NewsArticle", "TechArticle", "Report", "ScholarlyArticle"}
_ARTICLE_PATH = re.compile(r"/(blog|news|article|articles|post|posts|insights|stories|press)(/|$)|/20\d\d/", re.I)


def _walk_jsonld(node, acc):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _JSONLD_KEYS and isinstance(v, (str, int, float)):
                acc[k].append(str(v))
            if k == "author" and v:
                acc["has_author"] = True
            _walk_jsonld(v, acc)
    elif isinstance(node, list):
        for it in node:
            _walk_jsonld(it, acc)


def parse_page(url: str, html: str, root: str, perf: dict | None = None) -> dict:
    soup = BeautifulSoup(html, "lxml")
    jsonld, jsonld_errors = [], 0
    ld = {k: [] for k in _JSONLD_KEYS}
    ld["has_author"] = False
    ld["reviews"] = False
    for sc in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        try:
            data = json.loads(sc.string or "")
        except (ValueError, TypeError):
            jsonld_errors += 1
            continue
        _walk_jsonld(data, ld)
        if re.search(r'AggregateRating|"@type"\s*:\s*"Review"', sc.string or ""):
            ld["reviews"] = True
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for it in items:
            if isinstance(it, dict) and it.get("@type"):
                t = it["@type"]
                jsonld.extend(t if isinstance(t, list) else [t])
    script_count = len(soup.find_all("script"))
    head = soup.head
    blocking = [x for x in (head.find_all("script", src=True) if head else [])
                if not x.has_attr("async") and not x.has_attr("defer") and (x.get("type") or "").lower() != "module"]
    md = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    canon = soup.find("link", rel=lambda v: v and "canonical" in v)
    viewport = bool(soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)}))
    stylesheets = len(soup.find_all("link", rel=lambda v: v and "stylesheet" in v))
    images = soup.find_all("img")
    img_stats = (len(images), sum(1 for i in images if i.get("alt") is None), sum(1 for i in images if not (i.get("width") and i.get("height"))))

    # dates from structured sources
    found_dates = []
    for m in soup.find_all("meta"):
        key = (m.get("property") or m.get("name") or m.get("itemprop") or "").lower()
        if key in _DATE_META and (d := D.parse_any(m.get("content", ""))):
            found_dates.append({"date": d.isoformat(), "src": f"meta {key}"})
    for t in soup.find_all("time"):
        if d := D.parse_any(t.get("datetime") or t.get_text()):
            found_dates.append({"date": d.isoformat(), "src": "time tag"})
    for k in ("datePublished", "dateModified"):
        for v in ld[k]:
            if d := D.parse_any(v):
                found_dates.append({"date": d.isoformat(), "src": f"structured data {k}"})
    og_type = soup.find("meta", attrs={"property": "og:type"})
    author = bool(soup.find("meta", attrs={"name": re.compile("^author$", re.I)}) or soup.find(attrs={"rel": "author"})
                  or soup.find(attrs={"itemprop": "author"}) or ld["has_author"])

    links, contact_links, trust = [], False, set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith(("mailto:", "tel:")):
            contact_links = True
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        links.append(_normalize(urljoin(url, href)))
        tok = (href + " " + a.get_text(" ", strip=True)).lower()
        # English plus common Azerbaijani, Turkish and Russian link words
        for name, pat in (("about", r"about|our-story|who-we-are|\bteam\b|our team|haqq[ıi]m[ıi]zda|haqq[ıi]nda|hakk[ıi]m[ıi]zda|şirkət|sirket|о нас|о компании|o-nas"),
                          ("privacy", r"privacy|m[əe]xfilik|gizlilik|konfidensial|конфиденциальн|политика"),
                          ("terms", r"terms|conditions|qaydalar|şərtlər|sertler|şartlar|условия|соглашение"),
                          ("contact", r"contact|[əe]laq[əe]|iletişim|iletisim|контакт|kontakt")):
            if re.search(pat, tok):
                trust.add(name)
    resources, seen_res = [], set()

    def _res(u, kind):
        if u and not u.startswith(("data:", "blob:", "javascript:")):
            u = urljoin(url, u.strip()).split("#")[0]
            if u.startswith(("http://", "https://")) and u not in seen_res:
                seen_res.add(u)
                resources.append({"url": u, "kind": kind})

    for t in soup.find_all("script", src=True):
        _res(t["src"], "js")
    for t in soup.find_all("link", href=True):
        rel_ = " ".join(t.get("rel") or []).lower()
        if "stylesheet" in rel_:
            _res(t["href"], "css")
        elif "preload" in rel_ and (t.get("as") or "").lower() == "font":
            _res(t["href"], "font")
    for t in images:
        _res(t.get("src") or (t.get("srcset") or "").split(",")[0].split(" ")[0], "img")
    vp = soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)})
    viewport_content = (vp.get("content") or "").lower() if vp else ""
    lazy_imgs = sum(1 for i in images if (i.get("loading") or "").lower() == "lazy")
    iframes = len(soup.find_all("iframe"))
    headings = [(int(h.name[1]), re.sub(r"\s+", " ", h.get_text(" ", strip=True))[:200])
                for h in soup.find_all(re.compile("^h[1-6]$"))]
    for t in soup(["script", "style", "noscript", "template", "svg"]):
        t.decompose()
    text = re.sub(r"[ \t\r\f\v]+", " ", soup.get_text("\n"))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    for d, snippet in D.labeled_dates(text):
        found_dates.append({"date": d.isoformat(), "src": f"visible text: \"{snippet}\""})
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    internal = sorted({l for l in links if l.startswith(root)})
    rel = rel_path(root, url)
    return {
        "url": url, "rel": rel, "ok": True, "status": 200, "error": None,
        "title": title, "meta_description": (md.get("content") or "").strip() if md else "",
        "has_meta_description": bool(md), "canonical": canon.get("href") if canon else "",
        "lang": (soup.html.get("lang") if soup.html else "") or "",
        "headings": headings, "text": text[:80000], "word_count": len(text.split()),
        "internal_links": internal, "external_link_count": len({l for l in links if not l.startswith(root)}),
        "jsonld_types": jsonld, "jsonld_errors": jsonld_errors, "jsonld_values": {k: ld[k] for k in ("telephone", "email", "price", "streetAddress")},
        "script_count": script_count, "blocking_scripts": len(blocking), "stylesheets": stylesheets, "viewport": viewport,
        "images_total": img_stats[0], "images_missing_alt": img_stats[1], "images_no_dims": img_stats[2],
        "injection_hits": find_injection(text),
        "dates": found_dates, "copyright_year": D.copyright_year(text),
        "emails": EMAIL_FIND(text), "phones": D.find_phones(text), "addresses": [m.group(0) for m in D.ADDRESS.finditer(text)][:3],
        "contact_links": contact_links, "trust_links": sorted(trust), "has_author": author,
        "social_links": sorted({m.group(1).lower() for l in links if (m := _SOCIAL.search(l))}),
        "has_reviews": ld["reviews"] or bool(_REVIEWS.search(text)),
        "article_like": bool(set(jsonld) & _ARTICLE_TYPES) or bool(og_type and (og_type.get("content") or "").lower() == "article") or bool(_ARTICLE_PATH.search(rel)),
        "perf": perf or {}, "resources": resources[:80], "viewport_content": viewport_content, "lazy_imgs": lazy_imgs, "iframes": iframes,
    }


def EMAIL_FIND(text):
    return sorted(set(D.EMAIL.findall(text or "")))[:3]


def _probe(client, url: str) -> dict:
    """Size of one page resource. Safe: validated on every hop, headers first, body read only if no Content-Length."""
    cur = url
    try:
        for _ in range(4):
            cur = validate_url(cur)
            with client.stream("GET", cur) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    cur = urljoin(cur, r.headers["location"])
                    continue
                if r.status_code >= 400:
                    return {"ok": False}
                n = r.headers.get("content-length")
                if n and n.isdigit():
                    return {"ok": True, "bytes": int(n), "url": cur}
                size = 0
                for chunk in r.iter_raw():
                    size += len(chunk)
                    if size > 400_000:
                        break
                return {"ok": True, "bytes": size, "url": cur, "estimated": True}
    except Exception:
        pass
    return {"ok": False}


def measure_weight(page: dict, deadline_s: float = 14.0) -> dict:
    """Download size of the files a page loads (scripts, styles, images, fonts). Proxy for mobile loading cost."""
    res = page.get("resources", [])[:60]
    t0 = time.monotonic()
    try:
        import h2  # noqa: F401
        use_h2 = True
    except ImportError:
        use_h2 = False
    sizes = []
    host = urlparse(page["url"]).hostname or ""
    own = DOM.registrable(host)
    with httpx.Client(timeout=httpx.Timeout(6.0), follow_redirects=False, headers={"User-Agent": config.USER_AGENT, "Accept": "*/*"},
                      trust_env=False, http2=use_h2) as client:
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = [(r, ex.submit(_probe, client, r["url"])) for r in res]
            for r, f in futs:
                try:
                    out = f.result(timeout=max(0.5, deadline_s - (time.monotonic() - t0)))
                except Exception:
                    out = {"ok": False}
                sizes.append((r, out))
    by_kind = {"js": 0, "css": 0, "img": 0, "font": 0}
    big = []
    third = set()
    for r, o in sizes:
        h = urlparse(r["url"]).hostname or ""
        if h and DOM.registrable(h) != own:
            third.add(DOM.registrable(h))
        if o.get("ok"):
            by_kind[r["kind"]] += o["bytes"]
            big.append((o["bytes"], r["url"], r["kind"]))
    big.sort(reverse=True)
    html_bytes = (page.get("perf") or {}).get("bytes") or 0
    return {"measured": True, "files": len(res), "files_read": sum(1 for _, o in sizes if o.get("ok")), "html": html_bytes,
            "js": by_kind["js"], "css": by_kind["css"], "img": by_kind["img"], "font": by_kind["font"],
            "total": html_bytes + sum(by_kind.values()), "third_party_hosts": len(third), "third_party": sorted(third)[:8],
            "big_files": [{"bytes": b, "url": u, "kind": k} for b, u, k in big[:5]], "images_over_400kb": sum(1 for b, _, k in big if k == "img" and b > 400_000),
            "seconds": round(time.monotonic() - t0, 2), "capped": len(page.get("resources", [])) > 60}


def _score_candidate(url: str, root: str) -> int:
    path = rel_path(root, url).lower()
    return max([w for k, w in PRIORITY_WORDS if k in path] or [0])


def _blocking_rule(robots_text: str, path: str) -> str:
    """The Disallow line that applies to our bot (its own group, else the '*' group) and matches `path`."""
    groups, agents, rules, last_was_agent = [], [], [], False
    for raw in robots_text.splitlines():
        line = raw.split("#")[0].strip()
        if ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if not last_was_agent and (agents or rules):
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(v.lower())
            last_was_agent = True
        else:
            last_was_agent = False
            if k == "disallow" and v:
                rules.append(v)
    groups.append((agents, rules))
    mine = [g for g in groups if config.BOT_NAME.lower() in g[0]] or [g for g in groups if "*" in g[0]]
    for _, rs in mine:
        for r in rs:
            if path.startswith(r):
                return f"Disallow: {r}"
    return ""


def run(urls: list[str], log=lambda m: None) -> dict:
    """Audit ONLY the links the user entered (1 to MAX_PAGES, all on one website). No link discovery."""
    urls = [validate_url(u) for u in urls if u and u.strip()]
    if not urls:
        raise UnsafeURL("Please enter at least one website link.")
    root = site_root(urls[0])
    clean = []
    for u in urls:
        u = _normalize(u)
        if u.rstrip("/") == root.rstrip("/"):
            u = root
        if not u.startswith(root):
            raise UnsafeURL("All links must be pages of the same website.")
        if u not in clean:
            clean.append(u)
    clean = clean[: config.MAX_PAGES]
    out = {"root": root, "start_url": clean[0], "urls": clean,
           "robots": {"found": False, "text": "", "ai_bots_blocked": [], "blocks_all": False},
           "sitemap": {"found": False, "urls": []}, "pages": [], "blocked": [], "errors": []}

    domain_future = ThreadPoolExecutor(max_workers=1).submit(DOM.lookup, urlparse(root).hostname)

    # robots.txt (respected for every requested page)
    rp = RobotFileParser()
    r = fetch(root + "robots.txt", accept=("text/",))
    if r.error is None and r.body.strip():
        rp.parse(r.body.splitlines())
        out["robots"].update(found=True, text=r.body[:5000])
        out["robots"]["ai_bots_blocked"] = [b for b in AI_BOTS if not rp.can_fetch(b, root)]
        out["robots"]["blocks_all"] = not rp.can_fetch(config.BOT_NAME, root)
        log("robots.txt read")
    else:
        rp.parse([])
        log("No robots.txt (everything allowed)")

    # sitemap: only checked for existence, never used to pick extra pages
    sm_urls = [u for u in (rp.site_maps() or []) if u.startswith(root)] or [root + "sitemap.xml"]
    sm = fetch(sm_urls[0], accept=("xml", "text/"))
    if sm.error is None and "<loc" in sm.body:
        out["sitemap"]["found"] = True

    to_fetch = []
    for u in clean:
        if rp.can_fetch(config.BOT_NAME, u):
            to_fetch.append(u)
        else:
            out["blocked"].append({"url": u, "rel": rel_path(root, u), "rule": _blocking_rule(out["robots"]["text"], urlparse(u).path)})
            log(f"Not fetched (robots.txt): {rel_path(root, u)}")
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(fetch, to_fetch))
    by_url = {}
    for u, res in zip(to_fetch, results):
        if res.error:
            by_url[u] = {"url": u, "rel": rel_path(root, u), "ok": False, "status": res.status, "error": res.error}
            log(f"Failed: {rel_path(root, u)} ({res.error})")
        else:
            try:
                by_url[u] = parse_page(res.url, res.body, root, res.perf)
                log(f"Read {rel_path(root, u)} ({by_url[u]['word_count']} words)")
            except Exception as e:  # a page with unusual markup must never take down the whole audit
                by_url[u] = {"url": u, "rel": rel_path(root, u), "ok": False, "status": res.status, "error": f"The page could not be processed ({type(e).__name__})"}
                log(f"Could not process {rel_path(root, u)} ({type(e).__name__})")
    out["pages"] = [by_url[u] for u in clean if u in by_url]  # keep the order the user entered
    ok_pages = [p for p in out["pages"] if p.get("ok")]
    if ok_pages:
        log("Measuring page weight (scripts, styles, images)")
        with ThreadPoolExecutor(max_workers=3) as ex:
            for p, w in zip(ok_pages, ex.map(measure_weight, ok_pages)):
                p["weight"] = w
    if not out["pages"]:
        out["errors"].append("None of the links could be read.")
    if config.PAGESPEED_API_KEY and ok_pages:
        log("Running Google PageSpeed (mobile)")
    out["pagespeed"] = PS.run(out["pages"])
    try:
        out["domain_info"] = domain_future.result(timeout=8)
    except Exception:
        out["domain_info"] = {"skipped": True, "reason": "Domain lookups timed out."}
    return out
