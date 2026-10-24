"""Central configuration. Secrets come from environment variables only.

.env lookup order: the repo folder first, then its parent folder.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT / ".env", ROOT.parent / ".env"):
    if _p.exists():
        load_dotenv(_p, override=False)


def _f(name, default):
    return float(os.getenv(name, default))


def _i(name, default):
    return int(os.getenv(name, default))


APP_PORT = _i("PORT", 8000)
APP_BASE_URL = os.getenv("APP_BASE_URL", f"http://127.0.0.1:{APP_PORT}").rstrip("/")

# --- Model -----------------------------------------------------------------
PORTKEY_API_KEY = os.getenv("PORTKEY_API_KEY", "")
PORTKEY_BASE_URL = "https://api.portkey.ai/v1"
AI_MODEL = "gpt-6-luna"  # the only model allowed on the course key; deliberately not configurable
# USD per million tokens: OpenAI's published standard price for gpt-6-luna (checked on the OpenAI API pricing
# page on 2026-10-23): $0.10 input, $0.01 cached input (10% of input), $0.50 output. Override in .env if it changes.
PRICE_INPUT_PER_M = _f("PRICE_INPUT_PER_M", 0.10)
PRICE_OUTPUT_PER_M = _f("PRICE_OUTPUT_PER_M", 0.50)

# Optional: real Lighthouse mobile speed test via Google PageSpeed Insights (free key from Google Cloud).
PAGESPEED_API_KEY = os.getenv("PAGESPEED_API_KEY", "")

# --- Crawl limits ----------------------------------------------------------
MAX_PAGES = _i("MAX_PAGES", 3)
FETCH_TIMEOUT = _f("FETCH_TIMEOUT", 8)
FETCH_DEADLINE = _f("FETCH_DEADLINE", 15)
MAX_BYTES = _i("MAX_BYTES", 1_500_000)
MAX_REDIRECTS = 4
PAGE_CHAR_BUDGET = _i("PAGE_CHAR_BUDGET", 5000)  # chars of page text sent to the LLM

# --- Spend / abuse controls ------------------------------------------------
MAX_LLM_CALLS = _i("MAX_LLM_CALLS", 6)
MAX_AUDIT_TOKENS = _i("MAX_AUDIT_TOKENS", 60000)
DAILY_AUDIT_CAP = _i("DAILY_AUDIT_CAP", 300)          # all visitors together, per day
DAILY_LIMIT_PER_USER = _i("DAILY_LIMIT_PER_USER", 5)   # analyses per visitor per day
# Your own computer (and course staff running the app locally) are never limited per user.
EXEMPT_IPS = {x.strip() for x in os.getenv("RATE_LIMIT_EXEMPT_IPS", "127.0.0.1,::1,localhost").split(",") if x.strip()}
# Set TRUST_PROXY=1 only when the app sits behind a proxy/host that sets X-Forwarded-For (most hosting services).
TRUST_PROXY = os.getenv("TRUST_PROXY", "").lower() in ("1", "true", "yes")
FIX_TOP_N = _i("FIX_TOP_N", 5)
EVAL_NONCE = ""  # set only by evaluation/evaluate.py

BOT_NAME = "AISearchDoctorBot"
USER_AGENT = f"{BOT_NAME}/1.0 (website readiness audit; respects robots.txt)"
DB_PATH = ROOT / "data" / "audits.db"
