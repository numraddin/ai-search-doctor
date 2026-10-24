"""Agent 4: Evidence Verifier & Safety Agent.

Deterministic. Every LLM claim is checked against the text that was actually crawled:
  * quotes must appear verbatim in the cited page,
  * "missing information" claims are grepped across ALL pages,
  * contradiction claims need two real, differing quotes,
  * claims that show signs of following injected instructions are rejected.
Anything that fails is rejected with a reason. Nothing unverified reaches the report.
"""
import re
import unicodedata

from . import content as C
from .findings import make_finding, rel_path, slug
from .safety import find_injection

DIM_BY_TYPE = {
    "missing_business_description": "answer_quality", "unclear_offering": "answer_quality", "inconsistent_terminology": "answer_quality",
    "missing_pricing": "answer_quality", "unanswered_question": "answer_quality", "ambiguous_content": "answer_quality",
    "price_contradiction": "credibility", "inconsistent_facts": "credibility",
    "missing_author_or_source": "credibility", "unsupported_claim": "credibility",
}
ABSENCE = {"missing_business_description", "missing_pricing", "missing_author_or_source", "unanswered_question"}
CONTRADICTION = {"price_contradiction", "inconsistent_facts"}
EFFORT = {"missing_pricing": "medium", "missing_contact_info": "low", "price_contradiction": "low", "inconsistent_facts": "low",
          "missing_business_description": "medium", "unanswered_question": "medium"}
# Several distinct issues of these types can exist on one page, so the key must include the title.
MULTI_PER_PAGE = {"unanswered_question", "other", "inconsistent_facts", "price_contradiction", "unsupported_claim",
                  "inconsistent_terminology", "ambiguous_content", "unclear_offering"}
_MONEY = re.compile(r"[$€£]\s?\d[\d,]*(?:\.\d+)?")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = s.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip()


def quote_in(quote: str, text_norm: str) -> bool:
    parts = [norm(p).strip(" \"'.,;:!?") for p in re.split(r"\.{3}|…", quote or "")]
    parts = [p for p in parts if len(p) >= 6]
    return bool(parts) and all(p in text_norm for p in parts)


class Verifier:
    def __init__(self, crawl: dict):
        self.root = crawl["root"]
        self.pages = {self._canon(p["url"]): p for p in crawl["pages"] if p.get("ok")}
        self.norm_text = {u: norm(p["text"]) for u, p in self.pages.items()}
        self.all_text = " ".join(self.norm_text.values())

    @staticmethod
    def _canon(u: str) -> str:
        u = (u or "").split("#")[0].split("?")[0]
        if u.endswith("index.html"):
            u = u[: -len("index.html")]
        return u.rstrip("/")

    def _page_for(self, url):
        u = self._canon(url)
        if u in self.pages:
            return u
        # tolerate a relative path such as "/pricing.html"
        for k in self.pages:
            if url and rel_path(self.root, k).rstrip("/") == url.rstrip("/"):
                return k
        return None

    def check_quote(self, url, quote):
        key = self._page_for(url)
        if key is None:
            return False, "cited page was not crawled"
        if not quote_in(quote, self.norm_text[key]):
            return False, "quote not found on the cited page"
        return True, ""

    # ---- findings ----------------------------------------------------------
    def verify_issue(self, it: dict):
        """Return (finding_or_None, rejection_or_None)."""
        itype = it.get("issue_type", "other")
        rej = lambda why: (None, {"title": it.get("title", ""), "issue_type": itype, "url": it.get("url", ""), "reason": why,
                                  "claimed_quotes": [q.get("quote", "") for q in it.get("quotes", [])][:2]})
        text_fields = " ".join(str(it.get(k, "")) for k in ("title", "why", "recommendation"))
        if find_injection(text_fields):
            return rej("The claim contains text that looks like injected instructions, not evidence.")
        page_key = self._page_for(it.get("url", ""))
        quotes = [q for q in it.get("quotes", []) if q.get("quote")]
        evidence, status, confidence, note = [], "confirmed", "high", ""

        if itype in ABSENCE and not quotes:
            terms = [t for t in it.get("searched_terms", []) if len(t.strip()) >= 3]
            if not terms:
                return rej("A 'missing information' claim must list the terms that were searched for.")
            found = [t for t in terms if norm(t) in self.all_text]
            if found:
                return rej("The terms " + ", ".join(f"\"{t}\"" for t in found[:3]) + " DO appear on the crawled pages, so the information is not missing.")
            url = it.get("url") if page_key else self.root
            status, confidence = "potential", "medium"
            note = "Only the pages you entered were searched, so the information may exist elsewhere on the site."
            evidence = [{"kind": "observation", "url": url, "text": f"Searched all {len(self.pages)} analyzed pages for: {', '.join(terms[:6])}. No matches."}]
        else:
            if not quotes:
                return rej("No supporting quote was provided.")
            for q in quotes:
                ok, why = self.check_quote(q.get("url"), q["quote"])
                if not ok:
                    return rej(f"Evidence check failed: {why}.")
                evidence.append({"kind": "quote", "url": self.pages[self._page_for(q["url"])]["url"], "text": q["quote"], "verified": True})
            if itype in CONTRADICTION:
                if len({norm(e["text"]) for e in evidence}) < 2:
                    return rej("A contradiction needs two different quotes.")
                if itype == "price_contradiction":
                    amounts = [set(_MONEY.findall(e["text"])) for e in evidence]
                    if not all(amounts) or len(set().union(*amounts)) < 2:
                        return rej("The quoted passages do not show two different prices.")
            note = f"{len(evidence)} quote(s) verified verbatim on the crawled pages."
        url = it.get("url") if page_key else self.root
        if page_key:
            url = self.pages[page_key]["url"]
        rel = rel_path(self.root, url)
        dim = DIM_BY_TYPE.get(itype) or ("credibility" if it.get("dimension") == "credibility" else "answer_quality")
        f = make_finding(dimension=dim, issue_type=itype, title=it["title"], severity=it.get("severity", "medium"), url=url, rel=rel,
                         evidence=evidence, why=it.get("why", ""), recommendation=it.get("recommendation", ""), source="llm",
                         status=status, confidence=confidence, key_suffix=slug(it["title"], 24) if itype in MULTI_PER_PAGE else "",
                         effort=EFFORT.get(itype, "medium"))
        f["verification_note"] = note
        return f, None

    # ---- key facts and questions -------------------------------------------
    def verify_facts(self, facts):
        out = {}
        for f in facts or []:
            name = f.get("fact")
            if name not in C.FACT_KEYS:
                continue
            ok = bool(f.get("found")) and f.get("quote") and self.check_quote(f.get("url"), f["quote"])[0]
            out[name] = {"fact": name, "found": bool(ok), "value": f.get("value", "") if ok else "",
                         "url": f.get("url", "") if ok else "", "quote": f.get("quote", "") if ok else "",
                         "downgraded": bool(f.get("found")) and not ok}
        for name in C.FACT_KEYS:
            out.setdefault(name, {"fact": name, "found": False, "value": "", "url": "", "quote": "", "downgraded": False})
        return list(out.values())

    def verify_questions(self, qs):
        res = []
        for q in qs or []:
            ok = bool(q.get("answered")) and q.get("answer_quote") and self.check_quote(q.get("url"), q["answer_quote"])[0]
            res.append({"question": q.get("question", ""), "answered": bool(ok), "url": q.get("url", "") if ok else "",
                        "answer_quote": q.get("answer_quote", "") if ok else "", "downgraded": bool(q.get("answered")) and not ok})
        return res


def run(crawl: dict, content_out: dict | None, code_findings: list[dict]) -> dict:
    v = Verifier(crawl)
    verified, rejected = [], []
    seen = {f["key"] for f in code_findings}
    for it in (content_out or {}).get("issues", [])[:10]:
        f, r = v.verify_issue(it)
        if r:
            rejected.append(r)
        elif f["key"] in seen:
            rejected.append({"title": it.get("title", ""), "issue_type": f["issue_type"], "url": f["url"], "reason": "Duplicate of an existing finding.", "claimed_quotes": []})
        else:
            seen.add(f["key"])
            verified.append(f)
    facts = v.verify_facts((content_out or {}).get("key_facts"))
    questions = v.verify_questions((content_out or {}).get("customer_questions"))
    return {
        "findings": code_findings + verified, "rejected": rejected, "key_facts": facts, "questions": questions,
        "stats": {"claims_checked": len((content_out or {}).get("issues", [])[:10]), "verified": len(verified), "rejected": len(rejected),
                  "facts_downgraded": sum(f["downgraded"] for f in facts), "answers_downgraded": sum(q["downgraded"] for q in questions)},
    }
