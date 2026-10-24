# AI Search Doctor

**Be the website AI recommends!** AI Search Doctor shows what stops AI tools from finding, trusting and citing your site, and what to fix. Enter up to three links from one website; six agents read exactly those pages, audit them, **verify every finding against the page text**, rank the fixes, and let you analyze again and **compare** the results to prove a fix worked.

> The AI referral score is an internal checklist score of how well the pages are set up to be found, trusted and cited by AI. It is not a prediction, a search ranking and does not predict visibility in ChatGPT, Claude or Gemini.

## Run it (about 3 minutes)

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Put your key in a `.env` file **in this folder or one folder above it** (copy `.env.example`):

```
PORTKEY_API_KEY=your-key-here
```

Start the app and open http://127.0.0.1:8000

```bash
python run.py
```

Without a key the app still runs the rule-based checks (agents 1, 2, 4, 5) and clearly reports that the AI agents (3 and 6) did not run.

## Test sites (paste the links into the box)

The app serves five test websites from the `test_sites/` folder. Add the links below (use "Add another link" for each page), or edit the files and retest.

| Site | Pages to enter (prefix `http://127.0.0.1:8000/demo`) | What it checks |
|---|---|---|
| **A: Lumen Desk Lamps** | `/a/`, `/a/products.html`, `/a/contact.html` | Well-structured site; expect few issues |
| **B: BrightPath Tutoring** | `/b/`, `/b/more.html`, `/b/info.html` | Vague headings, missing metadata, prices and answers |
| **C: NovaFit Gym** | `/c/`, `/c/pricing.html`, `/c/private/members-pricing.html` | Conflicting prices, a robots.txt-blocked page, a hidden prompt-injection instruction |
| **D: Meridian Accounting** | `/d/`, `/d/pricing.html`, `/d/team.html` | Technically clean but facts contradict |
| **E: before/after example** | `/e/`, `/e/more.html`, `/e/info.html` | Audit these, copy the files from `test_sites/e_fix/` over `test_sites/e/`, audit again, then open *Compare* (score 35 to 99 in the report) |
| **B after fixes** | `/b_fixed/`, `/b_fixed/pricing.html`, `/b_fixed/tutors.html` | Retest target: audit B, then this, then open *Compare* |

Agent activity and token/cost tabs are hidden from regular visitors. Open http://127.0.0.1:8000/?details=1 to see them (used for the report).

## The six agents

| # | Agent | Uses model? |
|---|---|---|
| 1 | Crawler & Access: URL safety, robots.txt, fetch the (up to 3) links you entered, extract data | No (code) |
| 2 | Site Signals Analyzer: readability, speed and mobile signals, freshness, credibility signals, structured data | No (rules) |
| 3 | Content & Answer-Readiness Auditor: clarity, customer questions, contradictions | Yes |
| 4 | Evidence Verifier & Safety: checks every claim against crawled text, rejects unsupported or injected claims | No (code) |
| 5 | Prioritizer & Reporter: documented scoring and ranking | No (code) |
| 6 | Fix Writer: drafts fixes for the top verified issues only | Yes |

Flow: 1 → (2 ∥ 3) → 4 → 5 → 6. Agents pass structured data, not free text.

## Safety

- Website content is untrusted: wrapped in tags, the model is told never to follow it, and the verifier rejects any claim whose text looks like injected instructions or whose quotes are not on the page.
- SSRF protection: only public http(s) on ports 80/443; DNS is resolved and private, loopback and link-local addresses are refused, re-checked on **every redirect**.
- robots.txt respected; timeouts, 1.5 MB size cap, 3-page cap; HTML is parsed, never executed.
- Spend limits: max model calls and tokens per audit; **5 analyses per visitor per day** (kept on disk, so a restart does not reset it) and a 300-per-day cap for everyone together. Visitors see "come back tomorrow" when they reach it. Your own computer (127.0.0.1) is exempt, so staff running the app locally are never blocked. When hosting online behind a proxy, set `TRUST_PROXY=1` so each visitor is counted separately. Change the numbers with `DAILY_LIMIT_PER_USER` and `DAILY_AUDIT_CAP`.

Run the safety tests: `pytest tests` (an adversarial injection test is in `tests/test_safety.py`; Site C is the live demonstration).

## Configuration

See `.env.example`. The app calls only `gpt-6-luna` through the Portkey gateway using `PORTKEY_API_KEY`. Cost estimates use OpenAI's published `gpt-6-luna` prices ($0.10 per million input tokens, $0.01 cached input, $0.50 per million output tokens, checked 2026-10-23). Change `PRICE_INPUT_PER_M` / `PRICE_OUTPUT_PER_M` in `.env` if prices change. Costs shown in the app come from the token counts the API returns for each audit.

## Optional settings

- `PAGESPEED_API_KEY` (free Google Cloud key): adds Google's real Lighthouse mobile speed test and blends it into the Speed & mobile score. Without it the app measures page weight itself.
- Domain credibility uses free public sources with no key: RDAP (domain age), the Tranco ranking and public DNS (SPF/DMARC). If a source is unreachable the check is skipped, never penalized.

## Known limits

- JavaScript-rendered pages are reported as "not extractable"; they are not executed.
- "Missing information" findings are only as complete as the (up to 3) pages you enter, so they are labeled *potential*.
- DNS is validated before each request but not pinned to the connection (a DNS-rebinding race is theoretically possible).
- Test sites are served by the app itself; real sites are fetched live.
- Speed is measured from file sizes (or Lighthouse when a key is set), not on a real phone. Link-word checks cover English, Azerbaijani, Turkish and Russian.

## Project report

The written report and slides are submitted separately on Canvas. The measurements behind them can be reproduced with `python evaluation/evaluate.py --runs 3` and `python evaluation/cost_experiment.py --runs 3` (the app must be running).
