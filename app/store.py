"""SQLite persistence of audit results (for history and before/after comparison)."""
import json
import sqlite3
import threading

from . import config

_lock = threading.Lock()


def _conn():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(config.DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS audits (id TEXT PRIMARY KEY, url TEXT, domain TEXT, created_at TEXT,
                 score INTEGER, issues INTEGER, result TEXT)""")
    c.execute("CREATE TABLE IF NOT EXISTS usage (day TEXT, user TEXT, n INTEGER, PRIMARY KEY (day, user))")
    return c


def save(result: dict):
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO audits VALUES (?,?,?,?,?,?,?)",
                  (result["id"], result["url"], result.get("site", result["domain"]), result["timestamp"], result["score"]["overall"],
                   len(result["findings"]), json.dumps(result)))


def get(audit_id: str):
    with _lock, _conn() as c:
        row = c.execute("SELECT result FROM audits WHERE id=?", (audit_id,)).fetchone()
    return json.loads(row[0]) if row else None


def list_audits(limit: int = 30):
    with _lock, _conn() as c:
        rows = c.execute("SELECT id,url,domain,created_at,score,issues,json_array_length(json_extract(result,'$.urls')) FROM audits ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(zip(("id", "url", "domain", "timestamp", "score", "issues", "pages"), r)) for r in rows]


def _today():
    import time
    return time.strftime("%Y-%m-%d")


def usage_today(user: str) -> int:
    with _lock, _conn() as c:
        row = c.execute("SELECT n FROM usage WHERE day=? AND user=?", (_today(), user)).fetchone()
    return row[0] if row else 0


def try_use(user: str | None, user_limit: int, global_limit: int) -> str | None:
    """Count one analysis. Returns None if allowed, else 'user' or 'global' (the limit that was hit). Nothing is counted when refused."""
    day = _today()
    with _lock, _conn() as c:
        g = c.execute("SELECT n FROM usage WHERE day=? AND user='__all__'", (day,)).fetchone()
        if g and g[0] >= global_limit:
            return "global"
        if user is not None:
            u = c.execute("SELECT n FROM usage WHERE day=? AND user=?", (day, user)).fetchone()
            if u and u[0] >= user_limit:
                return "user"
            c.execute("INSERT INTO usage VALUES (?,?,1) ON CONFLICT(day,user) DO UPDATE SET n=n+1", (day, user))
        c.execute("INSERT INTO usage VALUES (?,?,1) ON CONFLICT(day,user) DO UPDATE SET n=n+1", (day, "__all__"))
    return None
