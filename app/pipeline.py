"""Orchestrator: runs the six agents, hands structured output from one to the next, tracks status."""
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse

from . import config, content, crawler, fixwriter, reporter, store, technical, verifier
from .llm import LLM, BudgetExceeded, LLMUnavailable
from .safety import UnsafeURL, validate_url

AGENTS = [
    ("crawler", "1. Crawler & Access Agent", "Checks URL safety and robots.txt, reads the links you entered (up to 3), extracts text, headings, metadata and structured data."),
    ("technical", "2. Site Signals Analyzer", "Measures readability, speed and mobile signals, freshness, credibility signals and structured data with fixed rules."),
    ("content", "3. Content & Answer-Readiness Auditor", "Model reads the pages, judges clarity, writes likely customer questions and checks if they are answered."),
    ("verifier", "4. Evidence Verifier & Safety Agent", "Checks every model claim against the crawled text. Rejects unsupported claims and injected instructions."),
    ("reporter", "5. Prioritizer & Reporter", "Scores six areas with documented rules and ranks issues by severity and effort."),
    ("fixwriter", "6. Fix Writer", "Model drafts ready-to-paste fixes for the top verified issues only."),
]


_PAGE_KEYS = ("url", "rel", "ok", "status", "error", "title", "word_count", "has_meta_description", "perf", "script_count", "blocking_scripts",
              "viewport", "dates", "copyright_year", "jsonld_types", "jsonld_errors", "emails", "phones", "addresses", "contact_links",
              "trust_links", "has_author", "article_like", "images_total", "images_missing_alt", "social_links", "has_reviews", "weight", "lazy_imgs", "iframes")


def _page_summary(p: dict) -> dict:
    out = {k: p.get(k) for k in _PAGE_KEYS}
    out["h1_count"] = sum(1 for lvl, _ in p.get("headings", []) if lvl == 1)
    return out


def new_job(urls) -> dict:
    urls = [urls] if isinstance(urls, str) else list(urls)
    return {"id": uuid.uuid4().hex[:10], "url": urls[0], "urls": urls, "status": "running", "error": None, "result": None, "events": [],
            "agents": {a: {"id": a, "name": n, "role": r, "status": "pending", "summary": "", "seconds": None, "notes": []} for a, n, r in AGENTS}}


class _Step:
    """Context manager that records an agent's status, timing and failure without crashing the audit."""

    def __init__(self, job, agent):
        self.job, self.a = job, job["agents"][agent]

    def __enter__(self):
        self.a["status"], self.t0 = "running", time.monotonic()
        self.job["events"].append({"t": round(time.time(), 1), "agent": self.a["id"], "msg": "started"})
        return self

    def note(self, msg):
        self.a["notes"].append(msg)
        self.job["events"].append({"t": round(time.time(), 1), "agent": self.a["id"], "msg": msg})

    def done(self, summary):
        self.a["status"], self.a["summary"] = "done", summary

    def fail(self, summary):
        self.a["status"], self.a["summary"] = "failed", summary

    def skip(self, summary):
        self.a["status"], self.a["summary"] = "skipped", summary

    def __exit__(self, et, ev, tb):
        self.a["seconds"] = round(time.monotonic() - self.t0, 1)
        if et is not None and self.a["status"] == "running":
            self.fail(f"Unexpected error: {type(ev).__name__}")
            return True  # swallow: one agent's crash must not kill the audit
        if self.a["status"] == "running":
            self.done("Completed")
        return False


def run_audit(job: dict) -> None:
    try:
        _run(job)
    except UnsafeURL as e:
        job.update(status="error", error=str(e))
    except Exception as e:  # last-resort guard; details are not shown to the user
        job.update(status="error", error=f"The audit stopped unexpectedly ({type(e).__name__}).")


def _run(job: dict) -> None:
    urls = [validate_url(u) for u in job["urls"]]
    url = urls[0]
    llm, warnings = LLM(), []
    crawl = {}

    with _Step(job, "crawler") as s:
        crawl = crawler.run(urls, s.note)
        ok = [p for p in crawl["pages"] if p.get("ok")]
        s.done(f"{len(ok)} page(s) fetched, {len(crawl['blocked'])} blocked by robots.txt, {sum(1 for p in crawl['pages'] if not p.get('ok'))} failed.")
        warnings += crawl["errors"]
    if not crawl:
        raise UnsafeURL("We could not read that website because of an unexpected problem. Please try again, or try a different page.")
    pages_ok = [p for p in crawl["pages"] if p.get("ok")]

    code_findings, content_out, llm_ran = [], None, False

    def do_tech():
        with _Step(job, "technical") as s:
            fs = technical.run(crawl)
            s.done(f"{len(fs)} rule-based finding(s).")
            return fs

    def do_content():
        with _Step(job, "content") as s:
            if not pages_ok:
                s.skip("No readable pages to analyze.")
                return None
            try:
                out = content.run(crawl, llm)
                s.done(f"{len(out.get('issues', []))} candidate issue(s), {len(out.get('customer_questions', []))} customer question(s).")
                return out
            except (LLMUnavailable, BudgetExceeded) as e:
                s.fail(str(e))
                warnings.append(f"Content analysis incomplete: {e}")
                return None

    with ThreadPoolExecutor(max_workers=2) as ex:
        ft, fc = ex.submit(do_tech), ex.submit(do_content)
        code_findings, content_out = ft.result() or [], fc.result()
    llm_ran = content_out is not None

    verified = {"findings": code_findings, "rejected": [], "key_facts": [], "questions": [], "stats": {}}
    with _Step(job, "verifier") as s:
        verified = verifier.run(crawl, content_out, code_findings)
        st = verified["stats"]
        if llm_ran:
            s.done(f"Checked {st['claims_checked']} model claim(s): {st['verified']} verified, {st['rejected']} rejected; "
                   f"{st['facts_downgraded'] + st['answers_downgraded']} unsupported statement(s) downgraded.")
        else:
            s.skip("No model claims to verify. Rule-based findings are confirmed by construction.")
        inj = sum(len(p.get("injection_hits", [])) for p in pages_ok)
        if inj:
            s.note(f"Detected {inj} instruction-like text(s) aimed at AI in page content. Treated as data and reported, not obeyed.")

    with _Step(job, "reporter") as s:
        rep = reporter.run(verified["findings"], verified["key_facts"], verified["questions"], llm_ran, len(pages_ok), len(crawl["urls"]),
                           (crawl.get("pagespeed") or {}).get("average"))
        ov = rep["score"]["overall"]
        s.done((f"Overall AI referral score {ov}/100 (heuristic). " if ov is not None else "Not scored: no page could be read. ") + f"{len(rep['findings'])} issue(s) ranked.")

    with _Step(job, "fixwriter") as s:
        if not llm_ran:
            s.skip("Skipped because the AI analysis did not run.")
        else:
            try:
                n = fixwriter.run(rep["findings"], crawl, llm)
                s.done(f"Drafted {n} fix suggestion(s) for the top issues.")
            except (LLMUnavailable, BudgetExceeded) as e:
                s.fail(str(e))
                warnings.append(f"Fix suggestions incomplete: {e}")

    usage = llm.tracker.summary()
    per = usage["total"]["cost_usd"]
    for aid, rec in usage["by_agent"].items():
        job["agents"][aid]["tokens"], job["agents"][aid]["cost_usd"] = rec["input"] + rec["cache_read"] + rec["cache_write"] + rec["output"], rec["cost_usd"]
    projections = [{"audits": n, "cost_usd": round(per * n, 4)} for n in (1, 10, 100, 1000)]
    result = {
        "id": job["id"], "url": url, "urls": crawl["urls"], "domain": urlparse(url).netloc,
        "site": crawl["root"].split("://", 1)[-1].rstrip("/"), "root": crawl["root"],
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages": [_page_summary(p) for p in crawl["pages"]],
        "domain_info": crawl.get("domain_info", {}), "pagespeed": crawl.get("pagespeed", {"enabled": False}), "blocked": crawl["blocked"], "robots_found": crawl["robots"]["found"], "sitemap_found": crawl["sitemap"]["found"],
        "score": rep["score"], "counts": rep["counts"], "findings": rep["findings"], "rejected": verified["rejected"],
        "verification": verified["stats"], "key_facts": verified["key_facts"], "questions": verified["questions"],
        "business_summary": (content_out or {}).get("business_summary", ""),
        "agents": [job["agents"][a] for a, _, _ in AGENTS], "usage": usage, "projections": projections,
        "cost_assumptions": f"Model {config.AI_MODEL} at ${config.PRICE_INPUT_PER_M}/M input and ${config.PRICE_OUTPUT_PER_M}/M output tokens "
                            f"(cached reads at 10%); measured on this audit of {len(pages_ok)} page(s). Projections assume the same average audit.",
        "warnings": warnings,
    }
    store.save(result)
    job.update(result=result, status="done")
