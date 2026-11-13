"""جمع‌آوری وضعیت، ثبت آپتایم و ارسال اعلان قطعی/وصل مجدد."""
import asyncio
import html
import logging
import time

import aiohttp

from . import db
from .config import ADMIN_IDS
from .panel import Panel, PanelError

log = logging.getLogger("monitor")
LAST = {"snap": None}


async def tcp_ping(host, port, timeout=5):
    t = time.time()
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, int(port)), timeout)
        w.close()
        return int((time.time() - t) * 1000)
    except Exception:
        return None


async def geo(host):
    key = f"geo:{host}"
    if db.get(key):
        return db.get(key)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as s:
            async with s.get(f"http://ip-api.com/json/{host}?fields=status,country,countryCode") as r:
                d = await r.json()
        if d.get("status") == "success":
            cc = d["countryCode"].upper()
            flag = "".join(chr(0x1F1E6 + ord(c) - 65) for c in cc)
            v = f"{flag} {d['country']}"
            db.put(key, v)
            return v
    except Exception:
        pass
    return "🌐 نامشخص"


def host_addr(h):
    a = h.get("address")
    if isinstance(a, list):
        a = next((x for x in a if x and "{" not in x), None)
    if not a or "{" in a:
        return None
    return a


async def collect(p: Panel):
    snap = {"ts": int(time.time()), "panel": {"ok": False}, "system": {}, "nodes": [], "hosts": [], "overuse": [],
            "ips_supported": None, "errors": []}
    t0 = time.time()
    try:
        snap["system"] = await p.system()
        snap["panel"] = {"ok": True, "ms": int((time.time() - t0) * 1000)}
    except PanelError as e:
        snap["panel"] = {"ok": False, "kind": e.kind, "msg": str(e)}
        return snap

    # نودها
    try:
        nodes = await p.nodes()
    except PanelError as e:
        nodes = []
        snap["errors"].append(f"دریافت نودها: {e}")
    sem = asyncio.Semaphore(20)

    async def node_job(n):
        async with sem:
            ms = await tcp_ping(n.get("address"), n.get("port") or 62050) if n.get("address") else None
            loc = await geo(n.get("address", "")) if n.get("address") else "🌐 نامشخص"
        snap["nodes"].append({**n, "tcp_ms": ms, "location": loc})

    await asyncio.gather(*(node_job(n) for n in nodes))
    snap["nodes"].sort(key=lambda x: x.get("id", 0))

    # کانفیگ‌ها (هاست‌ها)
    try:
        hosts = await p.hosts()
    except PanelError as e:
        hosts = []
        snap["errors"].append(f"دریافت کانفیگ‌ها: {e}")
    seen = {}
    for h in hosts:
        if h.get("is_disabled") or h.get("status") == "disabled":
            continue
        a = host_addr(h)
        if not a:
            continue
        port = h.get("port") or 443
        seen.setdefault((a, port), []).append(h.get("remark") or a)

    async def host_job(k, remarks):
        async with sem:
            ms = await tcp_ping(*k)
        snap["hosts"].append({"address": k[0], "port": k[1], "remark": " / ".join(remarks[:2]), "tcp_ms": ms})

    await asyncio.gather(*(host_job(k, v) for k, v in list(seen.items())[:80]))

    # اتصال بیش‌ازحد
    max_ips = int(db.get("max_ips"))
    try:
        users = await p.users("active", 300)
        now = time.time()
        online = [u for u in users if u.get("online_at") and _age(u["online_at"], now) < 180]
        usem = asyncio.Semaphore(8)

        async def ips_job(u):
            async with usem:
                ips = await p.user_ips(u["username"])
            if ips is None:
                snap["ips_supported"] = False
                return
            snap["ips_supported"] = True
            if len(ips) > max_ips:
                snap["overuse"].append({"username": u["username"], "ips": [str(i) for i in ips][:10], "count": len(ips)})

        await asyncio.gather(*(ips_job(u) for u in online[:60]))
    except PanelError as e:
        snap["errors"].append(f"بررسی کاربران: {e}")
    return snap


def _age(ts, now):
    from datetime import datetime
    try:
        if isinstance(ts, (int, float)):
            return now - ts
        return now - datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 1e9


def targets_of(snap):
    """نام هدف -> (kind, ok, ms, detail)"""
    t = {"panel": ("panel", snap["panel"]["ok"], snap["panel"].get("ms"), snap["panel"].get("msg", ""))}
    if not snap["panel"]["ok"]:
        return t
    for n in snap["nodes"]:
        if n.get("status") == "disabled":
            continue
        t[f"node:{n.get('name')}"] = ("node", n.get("status") == "connected", n.get("tcp_ms"), n.get("message") or n.get("status") or "")
    for h in snap["hosts"]:
        t[f"config:{h['remark']} ({h['address']}:{h['port']})"] = ("config", h["tcp_ms"] is not None, h["tcp_ms"], "TCP connect failed")
    return t


def fmt_dur(sec):
    sec = int(sec)
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    return " ".join(x for x in (f"{d}روز" if d else "", f"{h}ساعت" if h else "", f"{m}دقیقه") if x)


async def notify(bot, text):
    if db.get("notify") != "1":
        return
    for a in ADMIN_IDS:
        try:
            await bot.send_message(a, text, parse_mode="HTML")
        except Exception as e:  # noqa
            log.warning("notify failed: %s", e)


LABEL = {"panel": "پنل", "node": "نود", "config": "کانفیگ"}


async def process(bot, snap):
    now = int(time.time())
    th = int(db.get("fail_th"))
    for name, (kind, ok, ms, detail) in targets_of(snap).items():
        db.add_check(name, kind, ok, ms, detail)
        st = db.get_state(name)
        title = f"<b>{LABEL[kind]}</b> <code>{html.escape(name.split(':', 1)[-1])}</code>"
        if ok:
            if st and not st["ok"] and st["alerted"]:
                down = fmt_dur(now - st["since"])
                db.add_event("ok", f"{LABEL[kind]} {name} وصل شد (قطعی {down})")
                await notify(bot, f"🟢 {title} دوباره <b>وصل شد</b>.\n⏱ مدت قطعی: {down}")
            db.set_state(name, 1, st["since"] if st and st["ok"] else now, 0, 0)
        else:
            fails = (st["fails"] if st else 0) + 1
            since = st["since"] if st and not st["ok"] else now
            alerted = bool(st and st["alerted"] and not st["ok"])
            if fails >= th and not alerted:
                alerted = True
                db.add_event("down", f"{LABEL[kind]} {name} قطع شد: {detail}")
                await notify(bot, f"🔴 {title} <b>قطع شد!</b>\n{html.escape(detail[:300])}\n\n🩺 برای راه‌حل: /start ← عیب‌یابی")
            db.set_state(name, 0, since, fails, alerted)

    for o in snap["overuse"]:
        key = f"ipalert:{o['username']}"
        if now - int(db.get(key) or 0) > 3600:
            db.put(key, now)
            db.add_event("warn", f"اتصال بیش‌ازحد: {o['username']} ({o['count']} IP)")
            await notify(bot, f"⚠️ کاربر/کانفیگ <code>{html.escape(o['username'])}</code> هم‌زمان از <b>{o['count']}</b> IP متصل است "
                              f"(حد مجاز {db.get('max_ips')}).")


async def loop(bot):
    while True:
        try:
            p = Panel()
            if p.configured:
                snap = await collect(p)
                LAST["snap"] = snap
                await process(bot, snap)
        except Exception:
            log.exception("monitor loop error")
        await asyncio.sleep(max(15, int(db.get("interval"))))
