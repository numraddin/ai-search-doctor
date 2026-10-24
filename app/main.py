"""FastAPI app: JSON API + static single-page UI + bundled demo websites."""
import hashlib
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import compare, config, crawler, pipeline, store
from .demo_sites import demo_urls
from .safety import UnsafeURL, validate_url

ROOT = config.ROOT
app = FastAPI(title="AI Search Doctor")
JOBS: dict[str, dict] = {}


class AuditRequest(BaseModel):
    url: str | None = None
    urls: list[str] | None = None


def client_ip(request: Request) -> str:
    """The visitor's address. X-Forwarded-For is only trusted when TRUST_PROXY is set (it can be faked otherwise)."""
    if config.TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if fwd:
            return fwd
    return request.client.host if request.client else "unknown"


def _user_key(ip: str) -> str | None:
    """Anonymous, stable id for per-user limits (a hash, so raw addresses are not stored). None = not limited."""
    if ip in config.EXEMPT_IPS:
        return None
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def _rate_limit(ip: str):
    refused = store.try_use(_user_key(ip), config.DAILY_LIMIT_PER_USER, config.DAILY_AUDIT_CAP)
    if refused == "user":
        raise HTTPException(429, f"You have used all {config.DAILY_LIMIT_PER_USER} free analyses for today. Please come back tomorrow.")
    if refused == "global":
        raise HTTPException(429, "We have reached today's capacity for free analyses. Please come back tomorrow.")


@app.get("/api/quota")
def quota(request: Request):
    key = _user_key(client_ip(request))
    if key is None:
        return {"limited": False}
    used = store.usage_today(key)
    return {"limited": True, "limit": config.DAILY_LIMIT_PER_USER, "used": used, "remaining": max(0, config.DAILY_LIMIT_PER_USER - used)}


@app.post("/api/audit")
def start_audit(req: AuditRequest, request: Request):
    raw = [u for u in (req.urls or ([req.url] if req.url else [])) if u and u.strip()]
    if not raw:
        raise HTTPException(400, "Please enter a website link.")
    if len(raw) > config.MAX_PAGES:
        raise HTTPException(400, f"You can add up to {config.MAX_PAGES} links.")
    try:
        urls = [validate_url(u) for u in raw]
        root = crawler.site_root(urls[0])
        if any(not (u.rstrip("/") + "/").startswith(root) for u in urls):
            raise UnsafeURL("All links must be pages of the same website.")
    except UnsafeURL as e:
        raise HTTPException(400, str(e))
    _rate_limit(client_ip(request))
    job = pipeline.new_job(urls)
    JOBS[job["id"]] = job
    threading.Thread(target=pipeline.run_audit, args=(job,), daemon=True).start()
    return {"id": job["id"]}


@app.get("/api/audit/{job_id}")
def get_audit(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        stored = store.get(job_id)
        if stored is None:
            raise HTTPException(404, "Audit not found.")
        return {"id": job_id, "status": "done", "result": stored, "events": [], "agents": {}, "error": None}
    return job


@app.get("/api/audits")
def list_audits():
    return store.list_audits()


@app.get("/api/compare")
def compare_audits(old: str, new: str):
    a, b = store.get(old), store.get(new)
    if not a or not b:
        raise HTTPException(404, "One of the audits was not found.")
    try:
        return compare.compare(a, b)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/presets")
def presets():
    return [
        {"id": "a", "name": "Well-structured site", "urls": demo_urls("a"), "blurb": "Lumen Desk Lamps. Clear and consistent."},
        {"id": "b", "name": "Poorly structured site", "urls": demo_urls("b"), "blurb": "BrightPath Tutoring. Vague, no metadata."},
        {"id": "c", "name": "Safety test", "urls": demo_urls("c"), "blurb": "NovaFit Gym. Conflicting prices, a blocked page, a prompt-injection trap."},
        {"id": "d", "name": "Subtle contradictions", "urls": demo_urls("d"), "blurb": "Meridian Accounting. Looks clean, facts disagree."},
        {"id": "b_fixed", "name": "Site B after fixes", "urls": demo_urls("b_fixed"), "blurb": "Retest target for the before/after comparison."},
    ]


@app.get("/api/config")
def public_config():
    return {"model": config.AI_MODEL, "ai_enabled": bool(config.PORTKEY_API_KEY), "max_pages": config.MAX_PAGES, "pagespeed": bool(config.PAGESPEED_API_KEY)}


app.mount("/demo", StaticFiles(directory=ROOT / "test_sites", html=True), name="demo")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(Path(__file__).parent / "static" / "favicon.png", media_type="image/png")


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.middleware("http")
async def no_stale_pages(request: Request, call_next):
    """Browsers must re-check the page and its scripts every time, so edits (such as testimonials) show up immediately."""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.exception_handler(Exception)
async def _unhandled(_, exc):
    return JSONResponse({"detail": "Something went wrong."}, status_code=500)
