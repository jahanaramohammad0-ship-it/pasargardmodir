"""ذخیره‌سازی تنظیمات، تاریخچه‌ی آپتایم، رویدادها و وضعیت اعلان‌ها (SQLite)."""
import os
import sqlite3
import threading
import time

from .config import DB_PATH

_lock = threading.Lock()
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
_c = sqlite3.connect(DB_PATH, check_same_thread=False)
_c.row_factory = sqlite3.Row
_c.executescript(
    """
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS checks(ts INTEGER, target TEXT, kind TEXT, ok INTEGER, ms INTEGER, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_checks ON checks(target, ts);
CREATE TABLE IF NOT EXISTS events(ts INTEGER, level TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS state(target TEXT PRIMARY KEY, ok INTEGER, since INTEGER, fails INTEGER, alerted INTEGER);
"""
)

DEFAULTS = {
    "interval": "60",      # فاصله‌ی بررسی (ثانیه)
    "cpu_th": "90",        # آستانه‌ی هشدار CPU/RAM (درصد)
    "max_ips": "3",        # حداکثر IP هم‌زمان برای هر کانفیگ
    "notify": "1",         # اعلان‌ها روشن
    "verify_ssl": "1",
    "fail_th": "2",        # بعد از چند بررسی ناموفق پشت‌سرهم اعلان قطعی بده
}


def get(k, default=None):
    with _lock:
        r = _c.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
    if r:
        return r["v"]
    return DEFAULTS.get(k, default)


def put(k, v):
    with _lock:
        _c.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
        _c.commit()


def add_check(target, kind, ok, ms=None, detail=""):
    with _lock:
        _c.execute("INSERT INTO checks VALUES(?,?,?,?,?,?)", (int(time.time()), target, kind, int(ok), ms, detail[:300]))
        _c.execute("DELETE FROM checks WHERE ts < ?", (int(time.time()) - 31 * 86400,))
        _c.commit()


def uptime(target, hours):
    since = int(time.time()) - hours * 3600
    with _lock:
        r = _c.execute("SELECT COUNT(*) n, SUM(ok) s FROM checks WHERE target=? AND ts>=?", (target, since)).fetchone()
    if not r or not r["n"]:
        return None
    return round(100.0 * (r["s"] or 0) / r["n"], 2)


def targets():
    with _lock:
        rows = _c.execute("SELECT DISTINCT target, kind FROM checks WHERE ts>=?", (int(time.time()) - 86400,)).fetchall()
    return [(r["target"], r["kind"]) for r in rows]


def add_event(level, text):
    with _lock:
        _c.execute("INSERT INTO events VALUES(?,?,?)", (int(time.time()), level, text[:500]))
        _c.execute("DELETE FROM events WHERE ts < ?", (int(time.time()) - 31 * 86400,))
        _c.commit()


def events(limit=15):
    with _lock:
        return _c.execute("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()


def get_state(target):
    with _lock:
        return _c.execute("SELECT * FROM state WHERE target=?", (target,)).fetchone()


def set_state(target, ok, since, fails, alerted):
    with _lock:
        _c.execute(
            "INSERT INTO state VALUES(?,?,?,?,?) ON CONFLICT(target) DO UPDATE SET "
            "ok=excluded.ok, since=excluded.since, fails=excluded.fails, alerted=excluded.alerted",
            (target, int(ok), since, fails, int(alerted)),
        )
        _c.commit()
