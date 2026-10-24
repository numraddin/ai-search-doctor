"""Agent 2: Site Signals Analyzer. Deterministic rules (no LLM) over the crawler's extracted data.

Covers six factors that matter for AI search eligibility and trust:
  readable, answer_quality (thin pages only), credibility, speed_mobile, freshness, structured_data.
Speed numbers are single-request proxies measured from this server, not Google Core Web Vitals.
"""
import re
from datetime import date

from .findings import make_finding
from .safety import is_demo_url

GENERIC_HEADINGS = {
    "welcome", "home", "more", "stuff", "about", "info", "information", "services", "products", "our stuff",
    "read more", "click here", "untitled", "page", "details", "other", "things", "misc", "main",
}
GENERIC_TITLES = {"home", "untitled", "welcome", "index", "page", "new page", "homepage"}
ORG_WORDS = ("organization", "business", "service", "store", "restaurant", "school", "hospital", "shop", "company", "corporation", "clinic")
STALE_DAYS, VERY_STALE_DAYS = 540, 1095


def _obs(url, text):
    return {"kind": "observation", "url": url, "text": text}


def run(crawl: dict) -> list[dict]:
    root, F = crawl["root"], []
    pages = [p for p in crawl["pages"] if p.get("ok")]
    first = pages[0] if pages else None
    today = date.today()

    def add(**kw):
        F.append(make_finding(source="code", **kw))

    # ---------------- 1. Can the page be read -----------------------------------
    for p in crawl["pages"]:
        if not p.get("ok"):
            if p.get("status") in (401, 403, 429):
                add(dimension="readable", issue_type="access_refused", title="The website's firewall refused our reader", severity="high", url=p["url"], rel=p["rel"],
                    evidence=[_obs(p["url"], f"Our reader (named AISearchDoctorBot) was refused with HTTP {p['status']}. The same address opens in a normal browser.")],
                    why="Some sites block every automated visitor they do not recognize. If your firewall does this, AI crawlers may be shut out too, which would keep your site out of AI answers. We respect the block and do not disguise our reader.",
                    recommendation="In your firewall or bot protection (for example Cloudflare), allow the user agent 'AISearchDoctorBot' and analyze again. Also check that AI crawlers such as GPTBot, ClaudeBot and PerplexityBot are allowed, otherwise AI tools may not be able to read your site either.",
                    effort="low")
                continue
            add(dimension="readable", issue_type="fetch_failed", title="Page could not be retrieved",
                severity="high" if p["rel"] == "/" else "medium", url=p["url"], rel=p["rel"],
                evidence=[_obs(p["url"], f"Fetch result: {p.get('error')}")],
                why="If automated systems cannot retrieve a page, none of its content can be read or cited.",
                recommendation="Make sure the page is publicly reachable, returns HTTP 200 and serves HTML.", effort="medium")
    for b in crawl["blocked"]:
        add(dimension="readable", issue_type="blocked_page", title="Page is blocked by robots.txt", severity="medium", url=b["url"], rel=b["rel"],
            evidence=[_obs(b["url"], f"robots.txt rule: {b['rule'] or 'matching Disallow rule'}")],
            why="Crawlers that obey robots.txt (including AI crawlers) will not read this page, so its information cannot be used in answers.",
            recommendation="If this page should be discoverable, remove or narrow the Disallow rule. If it is private, make sure nothing important is only published here.", effort="low")
    rb = crawl["robots"]
    if rb["blocks_all"]:
        add(dimension="readable", issue_type="robots_blocks_all", title="robots.txt blocks all crawling", severity="high", url=root + "robots.txt", rel="/robots.txt",
            evidence=[_obs(root + "robots.txt", "The site's rules disallow the whole site for our crawler.")],
            why="Nothing on the site can be read by compliant crawlers.", recommendation="Remove 'Disallow: /' for user agents you want to allow.", effort="low")
    elif rb["ai_bots_blocked"]:
        add(dimension="readable", issue_type="ai_bots_blocked", title="robots.txt blocks AI crawlers", severity="medium", url=root + "robots.txt", rel="/robots.txt",
            evidence=[_obs(root + "robots.txt", "Blocked user agents: " + ", ".join(rb["ai_bots_blocked"]))],
            why="Blocked AI crawlers cannot read your pages. This may be intentional; if it is not, AI tools will lack your information.",
            recommendation="Confirm the blocks are deliberate. Remove them for the crawlers you want to be able to read your site.", confidence="medium", effort="low")
    if not crawl["sitemap"]["found"] and pages:
        add(dimension="readable", issue_type="no_sitemap", title="No sitemap.xml found", severity="low", url=root, rel="/",
            evidence=[_obs(root, "No sitemap was found at /sitemap.xml or in robots.txt.")],
            why="A sitemap helps crawlers discover all important pages.", recommendation="Publish a sitemap.xml listing your key pages and reference it in robots.txt.", effort="low")

    titles = {}
    for p in pages:
        u, rel = p["url"], p["rel"]
        t, md = p["title"].strip(), p["meta_description"]
        titles.setdefault(t.lower(), []).append(rel)
        if not t:
            add(dimension="readable", issue_type="missing_title", title="Page has no title", severity="high", url=u, rel=rel,
                evidence=[_obs(u, "The <title> tag is missing or empty.")], why="The title is the first label any system uses to identify what a page is about.",
                recommendation="Add a unique, descriptive <title> (about 30-60 characters) naming the page topic and the business.", effort="low")
        elif t.lower() in GENERIC_TITLES or len(t) < 12:
            add(dimension="readable", issue_type="weak_title", title="Page title is too generic", severity="medium", url=u, rel=rel,
                evidence=[_obs(u, f'<title> is "{t}"')], why="A generic title does not tell people or machines what the page is.",
                recommendation="Rewrite the title to describe the page and the business, e.g. 'Pricing | Your Business Name'.", effort="low")
        elif len(t) > 70:
            add(dimension="readable", issue_type="long_title", title="Page title is very long", severity="low", url=u, rel=rel,
                evidence=[_obs(u, f"<title> is {len(t)} characters.")], why="Long titles are truncated and dilute the main topic.",
                recommendation="Shorten the title to under about 60 characters.", effort="low")
        if not p["has_meta_description"] or not md:
            add(dimension="readable", issue_type="missing_meta_description", title="Missing meta description", severity="medium", url=u, rel=rel,
                evidence=[_obs(u, "No <meta name=\"description\"> content found.")],
                why="The meta description is a ready-made summary that systems often reuse when describing the page.",
                recommendation="Add a 70-160 character description stating what the page offers and for whom.", effort="low")
        elif len(md) < 50:
            add(dimension="readable", issue_type="short_meta_description", title="Meta description is too short", severity="low", url=u, rel=rel,
                evidence=[_obs(u, f'Meta description is "{md}"')], why="A very short description carries little information.",
                recommendation="Expand it to 70-160 characters describing the page.", effort="low")
        h1s = [h for lvl, h in p["headings"] if lvl == 1]
        if not h1s:
            add(dimension="readable", issue_type="missing_h1", title="No main heading (H1)", severity="medium", url=u, rel=rel,
                evidence=[_obs(u, "No <h1> element found.")], why="The H1 tells readers and machines the page's main topic.",
                recommendation="Add exactly one H1 that names the page topic.", effort="low")
        elif len(h1s) > 1:
            add(dimension="readable", issue_type="multiple_h1", title="More than one main heading (H1)", severity="low", url=u, rel=rel,
                evidence=[_obs(u, "H1s: " + " | ".join(h1s[:4]))], why="Several H1s blur the page's main topic.",
                recommendation="Keep one H1 and demote the others to H2/H3.", effort="low")
        generic = [h for _, h in p["headings"] if h.strip(" .:!").lower() in GENERIC_HEADINGS]
        if generic:
            is_h1 = any(h.strip(" .:!").lower() in GENERIC_HEADINGS for h in h1s)
            add(dimension="readable", issue_type="ambiguous_heading", title="Headings are vague", severity="medium" if is_h1 else "low", url=u, rel=rel,
                evidence=[_obs(u, "Vague headings: " + " | ".join(sorted(set(generic))[:5]))],
                why="Headings like these do not say what the section contains, so systems cannot summarize or quote it accurately.",
                recommendation="Rename headings to say what the section is, e.g. 'Weekly Math Tutoring Plans and Prices'.", effort="low")
        levels = [lvl for lvl, _ in p["headings"]]
        if any(b - a > 1 for a, b in zip(levels, levels[1:])):
            add(dimension="readable", issue_type="heading_skip", title="Heading levels skip", severity="low", url=u, rel=rel,
                evidence=[_obs(u, "Heading level order: " + " > ".join(f"H{l}" for l in levels[:12]))],
                why="Skipped levels weaken the page outline.", recommendation="Use heading levels in order (H1, H2, H3).", effort="low")
        js = p["script_count"] >= 3 and p["word_count"] < 60
        if js:
            add(dimension="readable", issue_type="js_rendered", title="Content may be rendered by JavaScript", severity="high", url=u, rel=rel,
                evidence=[_obs(u, f"{p['word_count']} words of readable text; {p['script_count']} scripts.")],
                why="Most AI crawlers do not run JavaScript, so text added by scripts is invisible to them.",
                recommendation="Serve the key text in the HTML itself (server-side rendering).", effort="medium")
        elif p["word_count"] < 40:
            add(dimension="answer_quality", issue_type="thin_content", title="Page has very little text", severity="medium", url=u, rel=rel,
                evidence=[_obs(u, f"{p['word_count']} words of readable text.")],
                why="Pages with almost no text give AI systems little to work with.",
                recommendation="Add specific, factual text: what you offer, for whom, key details, prices.", effort="medium")
        if p["images_total"] >= 3 and p["images_missing_alt"] / p["images_total"] > 0.5:
            add(dimension="readable", issue_type="images_missing_alt", title="Most images lack alt text", severity="low", url=u, rel=rel,
                evidence=[_obs(u, f"{p['images_missing_alt']} of {p['images_total']} images have no alt attribute.")],
                why="Alt text is the only way text-based systems learn what an image shows.", recommendation="Add short descriptive alt text to meaningful images.", effort="low")
        if p["injection_hits"]:
            add(dimension="credibility", issue_type="prompt_injection_text", title="Text aimed at AI systems found in page content", severity="high", url=u, rel=rel,
                evidence=[{"kind": "quote", "url": u, "text": h} for h in p["injection_hits"][:2]],
                why="Instructions hidden in page text try to steer AI systems. Our agents ignored them, but other AI tools may follow them, and search systems may treat the page as manipulative.",
                recommendation="Remove any text that addresses AI models or tries to control their output.", effort="low")
    for t, rels in titles.items():
        if t and len(rels) > 1:
            add(dimension="readable", issue_type="duplicate_title", title="Several pages share the same title", severity="low", url=root, rel="/",
                key_suffix=",".join(sorted(rels))[:40], evidence=[_obs(root, f'Title "{t}" used on: ' + ", ".join(rels))],
                why="Identical titles make pages hard to tell apart.", recommendation="Give every page a unique title.", effort="low")

    # ---------------- 4. Speed and mobile (measured by loading the page's files) --
    def kb(n):
        return f"{n / 1000:,.0f} KB" if n < 1_000_000 else f"{n / 1_000_000:.1f} MB"

    for p in pages:
        u, rel, perf, w = p["url"], p["rel"], p.get("perf") or {}, p.get("weight") or {}
        ttfb, size = perf.get("ttfb"), perf.get("bytes") or 0
        if ttfb and ttfb > 0.8:
            add(dimension="speed_mobile", issue_type="slow_response", title=f"Server responds slowly ({ttfb:.1f}s)", severity="medium" if ttfb > 1.8 else "low", url=u, rel=rel,
                evidence=[_obs(u, f"Time until the server started responding: {ttfb:.2f}s (measured once from our server; Google rates up to 0.8s as good).")],
                why="Slow pages are crawled less reliably and give a poorer experience to the people AI sends to your site.",
                recommendation="Improve hosting or caching so the server answers in under 0.8 seconds.", confidence="medium", effort="medium")
        if size > 150_000:
            add(dimension="speed_mobile", issue_type="heavy_html", title="Page HTML is very large", severity="medium" if size > 500_000 else "low", url=u, rel=rel,
                evidence=[_obs(u, f"The HTML alone is {kb(size)}.")], why="Very large pages load slowly on phones and may be cut off by crawlers.",
                recommendation="Trim inline scripts and data, and move non-essential content to separate pages.", effort="medium")
        if w.get("measured"):
            top = "; ".join(f"{kb(f['bytes'])} {f['kind']}: …{f['url'][-55:]}" for f in w["big_files"][:3])
            tot = w["total"]
            if tot > 1_600_000:
                add(dimension="speed_mobile", issue_type="heavy_page", title=f"Page loads {kb(tot)} of files", severity="high" if tot > 5_000_000 else "medium" if tot > 3_000_000 else "low",
                    url=u, rel=rel, evidence=[_obs(u, f"Measured total: {kb(tot)} (HTML {kb(w['html'])}, scripts {kb(w['js'])}, styles {kb(w['css'])}, images {kb(w['img'])}, fonts {kb(w['font'])}). Largest: {top}")],
                    why="Every extra megabyte slows the page on mobile networks. Slow pages are abandoned by visitors and crawled less reliably. Chrome's Lighthouse treats about 1.6 MB as a good ceiling.",
                    recommendation="Compress and resize images, remove unused scripts and styles, and load below-the-fold content later.", effort="medium")
            if w["js"] > 300_000:
                add(dimension="speed_mobile", issue_type="heavy_scripts", title=f"{kb(w['js'])} of JavaScript to download", severity="high" if w["js"] > 1_200_000 else "medium" if w["js"] > 600_000 else "low",
                    url=u, rel=rel, evidence=[_obs(u, f"Measured JavaScript transfer size: {kb(w['js'])} across the script files this page loads.")],
                    why="JavaScript is the most expensive kind of file for phones: it must be downloaded, parsed and run before the page becomes usable.",
                    recommendation="Remove unused libraries, split code so each page loads only what it needs, and load non-essential scripts later.", effort="high")
            if w["images_over_400kb"]:
                n = w["images_over_400kb"]
                add(dimension="speed_mobile", issue_type="large_images", title=f"{n} image{'s are' if n > 1 else ' is'} over 400 KB", severity="medium" if n >= 3 else "low", url=u, rel=rel,
                    evidence=[_obs(u, f"{n} image file(s) larger than 400 KB. Largest files: {top}")],
                    why="Oversized images are the most common reason pages feel slow on phones.", recommendation="Resize images to the size they are shown at and use modern formats such as WebP or AVIF.", effort="low")
            nreq = w["files"] + 1
            if nreq > 60:
                add(dimension="speed_mobile", issue_type="many_requests", title=f"Page makes {nreq}+ separate requests", severity="medium" if nreq > 100 else "low", url=u, rel=rel,
                    evidence=[_obs(u, f"{w['files']} files referenced in the page{' (first 60 measured)' if w.get('capped') else ''}.")],
                    why="Each file adds a round trip, which is slow on mobile connections.", recommendation="Combine or remove files and avoid loading many small scripts or images.", effort="medium")
            if w["third_party_hosts"] > 5:
                add(dimension="speed_mobile", issue_type="third_party_load", title=f"Loads files from {w['third_party_hosts']} outside services", severity="medium" if w["third_party_hosts"] > 10 else "low", url=u, rel=rel,
                    evidence=[_obs(u, "Outside hosts: " + ", ".join(w["third_party"]))],
                    why="Analytics, ads and widgets from other companies slow the page and are outside your control.", recommendation="Remove third-party services you do not need.", effort="medium")
        if p["images_total"] >= 8 and not p.get("lazy_imgs"):
            add(dimension="speed_mobile", issue_type="no_lazy_loading", title="Images all load at once", severity="low", url=u, rel=rel,
                evidence=[_obs(u, f"{p['images_total']} images and none use loading=\"lazy\".")],
                why="Loading every image immediately slows the first screen.", recommendation="Add loading=\"lazy\" to images below the first screen.", effort="low")
        if p["script_count"] > 25:
            add(dimension="speed_mobile", issue_type="many_scripts", title="Page loads a lot of JavaScript files", severity="medium", url=u, rel=rel,
                evidence=[_obs(u, f"{p['script_count']} script elements.")], why="Heavy scripts slow the page, especially on phones.",
                recommendation="Remove unused scripts and load the rest with async or defer.", effort="medium")
        if p["blocking_scripts"] >= 3:
            add(dimension="speed_mobile", issue_type="render_blocking_scripts", title="Scripts delay the page from showing", severity="low", url=u, rel=rel,
                evidence=[_obs(u, f"{p['blocking_scripts']} scripts in the page head load without async or defer.")],
                why="Scripts loaded this way stop the page from displaying until they finish.", recommendation="Add async or defer to scripts, or move them to the end of the page.", effort="low")
        if not p["viewport"]:
            add(dimension="speed_mobile", issue_type="no_viewport", title="Page is not set up for mobile screens", severity="medium", url=u, rel=rel,
                evidence=[_obs(u, "No <meta name=\"viewport\"> tag found.")],
                why="Without it phones show a shrunken desktop page. Mobile-friendliness is a basic quality signal for search.",
                recommendation="Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> and check the layout on a phone.", effort="low")
        elif "user-scalable=no" in p.get("viewport_content", "").replace(" ", "") or "maximum-scale=1" in p.get("viewport_content", "").replace(" ", ""):
            add(dimension="speed_mobile", issue_type="zoom_blocked", title="Visitors cannot zoom on phones", severity="low", url=u, rel=rel,
                evidence=[_obs(u, f"Viewport setting: {p['viewport_content']}")], why="Blocking zoom hurts accessibility and is flagged by Google's mobile checks.",
                recommendation="Remove user-scalable=no and maximum-scale=1 from the viewport tag.", effort="low")
        if size > 60_000 and perf.get("encoding") not in ("gzip", "br", "deflate", "zstd"):
            add(dimension="speed_mobile", issue_type="no_compression", title="Page is sent without compression", severity="low", url=u, rel=rel,
                evidence=[_obs(u, f"{kb(size)} of HTML sent uncompressed.")], why="Compression makes pages download several times faster.",
                recommendation="Enable gzip or Brotli compression on your web server or CDN.", effort="low")
        if p["images_no_dims"] >= 5:
            add(dimension="speed_mobile", issue_type="images_no_dimensions", title="Images have no set size", severity="low", url=u, rel=rel,
                evidence=[_obs(u, f"{p['images_no_dims']} images are missing width and height attributes.")],
                why="The page layout jumps around as images load, which hurts the experience.", recommendation="Set width and height on images.", effort="low")

    ps = crawl.get("pagespeed") or {}
    for p in pages:
        r = (ps.get("pages") or {}).get(p["rel"])
        if r and r.get("ok") and r.get("score") is not None and r["score"] < 90:
            sc = r["score"]
            parts = []
            if r.get("lcp_ms"):
                parts.append(f"largest content shows after {r['lcp_ms'] / 1000:.1f}s")
            if r.get("tbt_ms") is not None:
                parts.append(f"page blocked for {r['tbt_ms']:.0f} ms")
            if r.get("cls") is not None:
                parts.append(f"layout shift {r['cls']:.2f}")
            opp = "; ".join(f"{o['title']} (about {o['saves_s']}s)" for o in r["opportunities"])
            f = make_finding(source="code", dimension="speed_mobile", issue_type="lighthouse_mobile", title=f"Google's mobile speed test scores this page {sc}/100",
                             severity="high" if sc < 50 else "medium", url=p["url"], rel=p["rel"],
                             evidence=[_obs(p["url"], f"Google PageSpeed (Lighthouse, mobile, lab data): {sc}/100. " + ", ".join(parts) + ".")],
                             why="This is a real mobile load test run by Google. Scores under 90 mean visitors on phones wait noticeably, and speed is a standard search-quality signal.",
                             recommendation=("Biggest savings Google found: " + opp + ".") if opp else "Reduce JavaScript, compress images and remove slow third-party scripts.",
                             effort="high")
            f["scored"] = False  # already counted through the blended Lighthouse score
            F.append(f)

    # ---------------- 5. Freshness ----------------------------------------------
    all_dates = []
    for p in pages:
        for d in p["dates"]:
            all_dates.append((date.fromisoformat(d["date"]), p, d["src"]))
    if pages:
        if not all_dates:
            cy = max([p["copyright_year"] for p in pages if p["copyright_year"]] or [0])
            if cy and cy <= today.year - 2:
                add(dimension="freshness", issue_type="stale_copyright", title=f"Footer still says © {cy}", severity="low", url=first["url"], rel=first["rel"],
                    evidence=[_obs(first["url"], f"The only date signal found is a copyright year of {cy}, and no page shows an update date.")],
                    why="An old year makes the whole site look unmaintained to people and to systems that weigh freshness.",
                    recommendation="Show a real 'Last updated' date on key pages and keep the footer year current.", effort="low")
            else:
                add(dimension="freshness", issue_type="no_dates", title="No visible publish or update date", severity="low", url=first["url"], rel=first["rel"],
                    evidence=[_obs(first["url"], f"No published or updated date found on the {len(pages)} page(s) analyzed (checked page markup, structured data and visible text).")],
                    why="AI systems favor recent information and cannot tell how current your prices or facts are without a date.",
                    recommendation="Add a visible 'Last updated: <date>' line to pages with prices, policies or news, and keep it accurate.", effort="low")
        else:
            newest, np_, src = max(all_dates, key=lambda x: x[0])
            age = (today - newest).days
            if age > STALE_DAYS:
                add(dimension="freshness", issue_type="stale_content", title=f"Newest date found is {newest.strftime('%b %Y')}", severity="high" if age > VERY_STALE_DAYS else "medium",
                    url=np_["url"], rel=np_["rel"],
                    evidence=[_obs(np_["url"], f"The most recent date on the pages you entered is {newest.isoformat()} ({src}), about {age // 30} months ago.")],
                    why="Content that looks old is less likely to be trusted or cited, and visitors may doubt the prices and facts.",
                    recommendation="Review the content, update anything out of date, and refresh the 'Last updated' date.", effort="medium")

    # ---------------- 3. Credibility and trust (on-page signals) -----------------
    if pages:
        if not any(p["emails"] or p["phones"] or p["addresses"] or p["contact_links"] for p in pages):
            add(dimension="credibility", issue_type="no_contact_details", title="No contact details found", severity="medium", status="potential", confidence="medium",
                url=first["url"], rel=first["rel"],
                evidence=[_obs(first["url"], f"Looked for an email, phone number, street address and mailto/tel links on the {len(pages)} page(s) entered. None found.")],
                why="Real contact details are one of the clearest trust signals for people and for AI systems deciding whether a business is real.",
                recommendation="Show an email, phone number and address (or service area) in plain text, ideally in the footer of every page.", effort="low")
        if not any("about" in p["trust_links"] or "about" in p["rel"].lower() or "about" in p["title"].lower() for p in pages):
            add(dimension="credibility", issue_type="no_about_page", title="No About page found", severity="low", status="potential", confidence="medium",
                url=first["url"], rel=first["rel"],
                evidence=[_obs(first["url"], "None of the pages entered is an About page and none links to one.")],
                why="An About page explains who is behind the site, which supports trust and lets AI describe you accurately.",
                recommendation="Add an About page (who you are, history, team, credentials) and link to it from the menu.", effort="medium")
        if not any(("privacy" in p["trust_links"]) or ("terms" in p["trust_links"]) for p in pages):
            add(dimension="credibility", issue_type="no_privacy_policy", title="No privacy policy or terms link", severity="low", status="potential", confidence="medium",
                url=first["url"], rel=first["rel"],
                evidence=[_obs(first["url"], "No link to a privacy policy or terms page on the pages entered.")],
                why="Legal pages are a standard sign of a legitimate site.", recommendation="Publish a privacy policy (and terms if you sell something) and link to it in the footer.", effort="low")
        for p in pages:
            if p["article_like"] and not p["has_author"]:
                add(dimension="credibility", issue_type="no_author", title="Article has no named author", severity="medium", url=p["url"], rel=p["rel"],
                    evidence=[_obs(p["url"], "The page looks like an article or post but has no author byline, author tag or author in structured data.")],
                    why="Named, credible authors are a key trust signal for informational content.",
                    recommendation="Add a visible byline with the author's name and a short bio or credentials.", effort="low")
        if root.startswith("http://") and not is_demo_url(root):
            add(dimension="credibility", issue_type="not_https", title="Site is not served over HTTPS", severity="medium", url=first["url"], rel=first["rel"],
                evidence=[_obs(first["url"], "The address starts with http:// not https://.")],
                why="Browsers warn visitors about insecure sites and search systems prefer secure ones.", recommendation="Install an SSL certificate and redirect all pages to https.", effort="medium")

        # --- domain credibility (public sources, no API key) ---
        di = crawl.get("domain_info") or {}
        if not di.get("skipped"):
            age = di.get("age_days")
            if age is not None and age < 730:
                add(dimension="credibility", issue_type="new_domain", title=f"Domain is only {max(1, age // 30)} months old" if age < 700 else "Domain is under two years old",
                    severity="medium" if age < 365 else "low", url=first["url"], rel=first["rel"],
                    evidence=[_obs(first["url"], f"{di['domain']} was first registered on {di['registered']} (public registry data, RDAP).")],
                    why="A new domain has little track record, which makes it harder for people and AI systems to trust. This improves with time.",
                    recommendation="Build trust signals in the meantime: a clear About page, real contact details, customer reviews and links to your official profiles.", effort="high")
            if di.get("dmarc") is False or di.get("spf") is False:
                missing = [n for n, v in (("SPF", di.get("spf")), ("DMARC", di.get("dmarc"))) if v is False]
                add(dimension="credibility", issue_type="no_email_auth", title=f"No {' or '.join(missing)} email protection", severity="low",
                    url=first["url"], rel=first["rel"], evidence=[_obs(first["url"], f"DNS lookup for {di['domain']} found no {' / '.join(missing)} record.")],
                    why="Without these records anyone can send email pretending to be your domain. Business domains normally have them, and it is a basic trust signal.",
                    recommendation="Ask your domain or email provider to add SPF and DMARC records.", effort="low")
        if root.startswith("https://") and any((p.get("perf") or {}).get("hsts") is False for p in pages):
            add(dimension="credibility", issue_type="no_hsts", title="Browsers are not told to always use HTTPS", severity="low", url=first["url"], rel=first["rel"],
                evidence=[_obs(first["url"], "The Strict-Transport-Security (HSTS) header is missing.")],
                why="HSTS tells browsers to refuse insecure connections to your site. It is a standard security signal.",
                recommendation="Enable the HSTS header on your web server or CDN.", effort="low")
        if not any(p["social_links"] for p in pages):
            add(dimension="credibility", issue_type="no_social_profiles", title="No links to official profiles", severity="low", status="potential", confidence="medium",
                url=first["url"], rel=first["rel"], evidence=[_obs(first["url"], "No links to LinkedIn, Facebook, X, Instagram, YouTube or similar profiles on the pages entered.")],
                why="Links to your official profiles let people and AI systems confirm the business is real and connect it to its other presence online.",
                recommendation="Link to your official profiles (for example LinkedIn and Google Business) in the footer.", effort="low")
        if not any(p["has_reviews"] for p in pages):
            add(dimension="credibility", issue_type="no_reviews", title="No customer reviews or testimonials shown", severity="low", status="potential", confidence="medium",
                url=first["url"], rel=first["rel"], evidence=[_obs(first["url"], f"No reviews, testimonials or ratings found on the {len(pages)} page(s) entered.")],
                why="Third-party proof such as reviews is one of the strongest trust signals for people and AI systems deciding who to recommend.",
                recommendation="Show real customer reviews or testimonials (with names or sources), and mark them up with Review structured data.", effort="medium")

    # ---------------- 6. Structured data -----------------------------------------
    for p in pages:
        if p["jsonld_errors"]:
            add(dimension="structured_data", issue_type="invalid_structured_data", title="Structured data is invalid", severity="medium", url=p["url"], rel=p["rel"],
                evidence=[_obs(p["url"], f"{p['jsonld_errors']} JSON-LD block(s) could not be parsed.")], why="Broken structured data is ignored.",
                recommendation="Validate the JSON-LD and fix syntax errors.", effort="medium")
    if first:
        types = sorted({t for p in pages for t in p["jsonld_types"] if isinstance(t, str)})
        if not types:
            add(dimension="structured_data", issue_type="missing_structured_data", title="No structured data (schema.org) on any page", severity="medium",
                url=first["url"], rel=first["rel"], evidence=[_obs(first["url"], f"No JSON-LD found on the {len(pages)} page(s) analyzed.")],
                why="Structured data states key facts (business name, products, prices, contact) in a machine-readable form that is hard to misread.",
                recommendation="Add JSON-LD for Organization/LocalBusiness and, where relevant, Product, Offer and FAQPage.", effort="medium")
        else:
            if not any(w in t.lower() for t in types for w in ORG_WORDS):
                add(dimension="structured_data", issue_type="no_org_schema", title="No business or organization markup", severity="low",
                    url=first["url"], rel=first["rel"], evidence=[_obs(first["url"], "Structured data types found: " + ", ".join(types))],
                    why="Organization or LocalBusiness markup tells AI who you are, where you are and how to contact you.",
                    recommendation="Add Organization or LocalBusiness JSON-LD with your name, address, phone and website.", effort="low")
            visible = " ".join(p["text"] for p in pages)
            vis_low, vis_digits = visible.lower(), re.sub(r"\D", "", visible)
            bad = []
            for p in pages:
                v = p["jsonld_values"]
                for tel in v["telephone"]:
                    if re.sub(r"\D", "", tel) not in vis_digits:
                        bad.append(f"telephone {tel}")
                bad += [f"email {e}" for e in v["email"] if e.lower() not in vis_low]
                bad += [f"street address {a}" for a in v["streetAddress"] if a.lower() not in vis_low]
                # a price of 0 means "free", which pages usually say in words (in any language), so it is not compared
                bad += [f"price {x}" for x in v["price"] if not re.fullmatch(r"0+([.,]0+)?", x.strip()) and not re.search(rf"(?<!\d){re.escape(x)}(?!\d)", visible)]
            bad = sorted(set(bad))
            if bad:
                add(dimension="structured_data", issue_type="structured_data_mismatch", title="Structured data does not match the visible page", severity="medium",
                    url=first["url"], rel=first["rel"],
                    evidence=[_obs(first["url"], "In the structured data but not in the visible text of the pages entered: " + "; ".join(bad[:5]))],
                    why="Google requires markup to match what visitors can see. Hidden or contradictory values can be ignored or treated as deceptive.",
                    recommendation="Make the markup and the visible text say exactly the same thing, or remove the values that are not shown.", effort="low")
    return F
