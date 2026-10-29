"""SQLite storage: projects, jobs, cost log. Plans live as JSON files next to the project."""
import json
import sqlite3
import threading
import time
import uuid

from .config import DB_PATH, DATA_DIR

_lock = threading.Lock()


def conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects(
              id TEXT PRIMARY KEY, created_at REAL, prompt TEXT, target_duration_sec INTEGER,
              video_path TEXT, probe_json TEXT, status TEXT);
            CREATE TABLE IF NOT EXISTS jobs(
              id TEXT PRIMARY KEY, project_id TEXT, kind TEXT, status TEXT, progress TEXT,
              error TEXT, created_at REAL, updated_at REAL);
            CREATE TABLE IF NOT EXISTS cost_log(
              id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, ts REAL, model TEXT, step TEXT,
              input_summary TEXT, output_summary TEXT, units TEXT, est_cost_usd REAL,
              status TEXT, latency_ms INTEGER);
            """
        )
        cols = [r[1] for r in c.execute("PRAGMA table_info(projects)")]
        if "manual_minutes" not in cols:
            c.execute("ALTER TABLE projects ADD COLUMN manual_minutes INTEGER")


def project_dir(pid):
    d = DATA_DIR / pid
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_project(prompt, target, video_path, probe, pid=None, manual_minutes=None):
    pid = pid or "p_" + uuid.uuid4().hex[:10]
    with _lock, conn() as c:
        c.execute(
            "INSERT INTO projects(id,created_at,prompt,target_duration_sec,video_path,probe_json,status,"
            "manual_minutes) VALUES(?,?,?,?,?,?,?,?)",
            (pid, time.time(), prompt, target, str(video_path), json.dumps(probe), "uploaded",
             manual_minutes),
        )
    return pid


def get_project(pid):
    with conn() as c:
        r = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["probe"] = json.loads(d.pop("probe_json"))
    return d


def set_project_status(pid, status):
    with _lock, conn() as c:
        c.execute("UPDATE projects SET status=? WHERE id=?", (status, pid))


def create_job(pid, kind):
    jid = "j_" + uuid.uuid4().hex[:10]
    now = time.time()
    with _lock, conn() as c:
        c.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)", (jid, pid, kind, "queued", "", None, now, now)
        )
    return jid


def update_job(jid, status=None, progress=None, error=None):
    with _lock, conn() as c:
        cur = dict(c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone())
        c.execute(
            "UPDATE jobs SET status=?, progress=?, error=?, updated_at=? WHERE id=?",
            (status or cur["status"], progress if progress is not None else cur["progress"],
             error if error is not None else cur["error"], time.time(), jid),
        )


def latest_job(pid, kind):
    with conn() as c:
        r = c.execute(
            "SELECT * FROM jobs WHERE project_id=? AND kind=? ORDER BY created_at DESC LIMIT 1",
            (pid, kind),
        ).fetchone()
    return dict(r) if r else None


def add_cost(pid, **e):
    with _lock, conn() as c:
        c.execute(
            "INSERT INTO cost_log(project_id,ts,model,step,input_summary,output_summary,units,"
            "est_cost_usd,status,latency_ms) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (pid, time.time(), e["model"], e["step"], e.get("input_summary", "")[:300],
             e.get("output_summary", "")[:300], e.get("units", ""), e.get("est_cost_usd", 0.0),
             e.get("status", "ok"), e.get("latency_ms", 0)),
        )


def cost_log(pid):
    with conn() as c:
        rows = c.execute("SELECT * FROM cost_log WHERE project_id=? ORDER BY id", (pid,)).fetchall()
    return [dict(r) for r in rows]
