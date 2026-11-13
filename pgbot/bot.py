import asyncio
import html
import logging
import time
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from . import db, monitor
from .config import ADMIN_IDS, BOT_TOKEN
from .diagnose import ICON, diagnose
from .panel import Panel, PanelError

router = Router()
e = html.escape


class IsAdmin(BaseFilter):
    async def __call__(self, ev) -> bool:
        return bool(ev.from_user and ev.from_user.id in ADMIN_IDS)


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class S(StatesGroup):
    waiting = State()


# ---------- کمک‌ها ----------
def kb(*rows):
    """دکمه‌های شیشه‌ای (Inline Keyboard)"""
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in r] for r in rows])


HOME = ("🏠 منوی اصلی", "home")


def human(b):
    b = float(b or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"


def bar(pct, n=10):
    pct = max(0, min(100, pct or 0))
    f = round(pct / 100 * n)
    return "█" * f + "░" * (n - f)


async def show(cb: CallbackQuery, text, markup):
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as ex:
        if "not modified" not in str(ex):
            raise
    await cb.answer()


async def fresh(cb: CallbackQuery = None):
    p = Panel()
    if not p.configured:
        return None
    snap = await monitor.collect(p)
    monitor.LAST["snap"] = snap
    return snap


NOT_CFG = "⚙️ ابتدا از «تنظیمات» آدرس، نام‌کاربری و رمز پنل را وارد کنید."


def main_menu():
    return kb(
        [("📊 داشبورد", "dash"), ("🩺 عیب‌یابی", "diag")],
        [("🖥 نودها", "nodes"), ("🌍 لوکیشن‌ها", "locs")],
        [("🔗 کانفیگ‌ها", "cfgs"), ("⏱ آپتایم", "up:24")],
        [("👥 اتصال بیش‌ازحد", "ips"), ("📜 لاگ‌ها و خطاها", "logs")],
        [("🛠 تنظیمات", "set")],
    )


# ---------- شروع ----------
@router.message(Command("start", "menu"))
async def start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("👋 <b>مدیر هوشمند پاسارگارد</b>\nیک بخش را انتخاب کنید:", reply_markup=main_menu())


@router.callback_query(F.data == "home")
async def home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(cb, "👋 <b>مدیر هوشمند پاسارگارد</b>\nیک بخش را انتخاب کنید:", main_menu())


# ---------- داشبورد ----------
@router.callback_query(F.data == "dash")
async def dash(cb: CallbackQuery):
    await cb.answer("در حال بررسی…")
    snap = await fresh()
    if snap is None:
        return await show(cb, NOT_CFG, kb([("🛠 تنظیمات", "set")], [HOME]))
    p = snap["panel"]
    if not p["ok"]:
        txt = f"📊 <b>داشبورد</b>\n\n🔴 پنل <b>قطع است</b>\n{e(p['msg'])}"
    else:
        s = snap["system"]
        mt, mu = s.get("mem_total"), s.get("mem_used")
        mem = mu / mt * 100 if mt and mu else 0
        cpu = s.get("cpu_usage") or 0
        nodes = [n for n in snap["nodes"] if n.get("status") != "disabled"]
        nok = sum(1 for n in nodes if n.get("status") == "connected")
        hok = sum(1 for h in snap["hosts"] if h["tcp_ms"] is not None)
        issues = [i for i in diagnose(snap) if i.level in ("crit", "warn")]
        txt = (
            f"📊 <b>داشبورد</b>  <i>{datetime.now():%H:%M:%S}</i>\n\n"
            f"🟢 پنل آنلاین — {p['ms']}ms  {('v' + str(s['version'])) if s.get('version') else ''}\n"
            f"CPU  {bar(cpu)} {cpu:.0f}%\nRAM  {bar(mem)} {mem:.0f}%\n\n"
            f"👥 کاربران: {s.get('online_users', '?')} آنلاین از {s.get('total_user', '?')}\n"
            f"🖥 نودها: {nok}/{len(nodes)} متصل\n"
            f"🔗 کانفیگ‌ها: {hok}/{len(snap['hosts'])} در دسترس\n"
            f"⚠️ اتصال بیش‌ازحد: {len(snap['overuse'])}\n\n"
            + (f"🩺 <b>{len(issues)} مشکل</b> پیدا شد." if issues else "✅ مشکلی وجود ندارد.")
        )
    await show(cb, txt, kb([("🩺 عیب‌یابی", "diag"), ("🔄 بررسی مجدد", "dash")], [HOME]))


# ---------- عیب‌یابی ----------
@router.callback_query(F.data == "diag")
async def diag(cb: CallbackQuery):
    await cb.answer("در حال عیب‌یابی…")
    snap = await fresh()
    if snap is None:
        return await show(cb, NOT_CFG, kb([("🛠 تنظیمات", "set")], [HOME]))
    parts = []
    for i in diagnose(snap):
        parts.append(f"{ICON[i.level]} <b>{e(i.title)}</b>\n" + (f"<i>{e(i.detail)}</i>\n" if i.detail else "") + f"🛠 {i.fix}")
    txt = "🩺 <b>نتیجه‌ی عیب‌یابی</b>\n\n" + "\n\n".join(parts)
    await show(cb, txt[:4000], kb([("🔄 بررسی مجدد", "diag")], [HOME]))


# ---------- نودها ----------
ST = {"connected": "🟢", "connecting": "🟡", "error": "🔴", "disabled": "⚪️", "limited": "🟠"}


@router.callback_query(F.data == "nodes")
async def nodes(cb: CallbackQuery):
    await cb.answer("در حال دریافت…")
    snap = await fresh()
    if snap is None:
        return await show(cb, NOT_CFG, kb([("🛠 تنظیمات", "set")], [HOME]))
    if not snap["panel"]["ok"]:
        return await show(cb, f"🔴 پنل قطع است\n{e(snap['panel']['msg'])}", kb([("🩺 عیب‌یابی", "diag")], [HOME]))
    rows = [[(f"{ST.get(n.get('status'), '❔')} {n.get('name')}  {n.get('tcp_ms') or '—'}ms", f"node:{n.get('id')}")] for n in snap["nodes"]]
    await show(cb, f"🖥 <b>نودها ({len(snap['nodes'])})</b>\nروی هر نود بزنید:", kb(*rows, [("🔄 بروزرسانی", "nodes")], [HOME]))


@router.callback_query(F.data.startswith("node:"))
async def node_detail(cb: CallbackQuery):
    nid = int(cb.data.split(":")[1])
    snap = monitor.LAST["snap"] or await fresh()
    n = next((x for x in (snap or {}).get("nodes", []) if x.get("id") == nid), None)
    if not n:
        return await show(cb, "نود پیدا نشد.", kb([("🖥 نودها", "nodes")]))
    nm = n.get("name")
    txt = (
        f"{ST.get(n.get('status'), '❔')} <b>{e(str(nm))}</b>\n"
        f"📍 {n['location']}\n🌐 <code>{e(str(n.get('address')))}:{n.get('port')}</code>\n"
        f"وضعیت: {n.get('status')}\n"
        f"تأخیر TCP: {n.get('tcp_ms') or 'ناموفق'}ms\n"
        f"نسخه Xray: {n.get('xray_version') or '—'}  | نود: {n.get('node_version') or '—'}\n"
        f"آپتایم 24س: {db.uptime(f'node:{nm}', 24) or '—'}%  | 7روز: {db.uptime(f'node:{nm}', 168) or '—'}%\n"
        + (f"\n❗️ <code>{e(n['message'])}</code>" if n.get("message") else "")
    )
    await show(cb, txt, kb([("🔌 Reconnect", f"nrec:{nid}")], [("🖥 نودها", "nodes"), HOME]))


@router.callback_query(F.data.startswith("nrec:"))
async def node_reconnect(cb: CallbackQuery):
    nid = int(cb.data.split(":")[1])
    try:
        await Panel().node_reconnect(nid)
        await cb.answer("دستور اتصال مجدد ارسال شد ✅", show_alert=True)
    except PanelError as ex:
        await cb.answer(f"ناموفق: {ex}", show_alert=True)


# ---------- لوکیشن‌ها ----------
@router.callback_query(F.data == "locs")
async def locs(cb: CallbackQuery):
    await cb.answer("در حال دریافت…")
    snap = await fresh()
    if snap is None or not snap["panel"]["ok"]:
        return await show(cb, "🔴 اطلاعات در دسترس نیست (پنل قطع یا تنظیم‌نشده).", kb([("🩺 عیب‌یابی", "diag")], [HOME]))
    groups = {}
    for n in snap["nodes"]:
        groups.setdefault(n["location"], []).append(n)
    lines = []
    for loc, ns in groups.items():
        up = sum(1 for n in ns if n.get("status") == "connected")
        lines.append(f"{loc}  —  {up}/{len(ns)} آنلاین")
        for n in ns:
            lines.append(f"   {ST.get(n.get('status'), '❔')} {e(str(n.get('name')))}  {n.get('tcp_ms') or '—'}ms  | 24س: {db.uptime('node:' + str(n.get('name')), 24) or '—'}%")
    await show(cb, "🌍 <b>لوکیشن‌ها</b>\n\n" + ("\n".join(lines) or "نودی ثبت نشده."), kb([("🔄 بروزرسانی", "locs")], [HOME]))


# ---------- کانفیگ‌ها ----------
@router.callback_query(F.data == "cfgs")
async def cfgs(cb: CallbackQuery):
    await cb.answer("در حال تست اتصال…")
    snap = await fresh()
    if snap is None or not snap["panel"]["ok"]:
        return await show(cb, "🔴 اطلاعات در دسترس نیست.", kb([("🩺 عیب‌یابی", "diag")], [HOME]))
    hs = sorted(snap["hosts"], key=lambda h: h["tcp_ms"] is not None)
    lines = [f"{'🟢' if h['tcp_ms'] is not None else '🔴'} {e(h['remark'])}  <code>{e(h['address'])}:{h['port']}</code>  {h['tcp_ms'] or 'قطع'}{'ms' if h['tcp_ms'] else ''}" for h in hs[:40]]
    await show(cb, f"🔗 <b>کانفیگ‌ها ({len(hs)})</b>\n<i>تست TCP از سرور ربات</i>\n\n" + "\n".join(lines), kb([("🔄 تست مجدد", "cfgs")], [HOME]))


# ---------- آپتایم ----------
@router.callback_query(F.data.startswith("up:"))
async def uptime(cb: CallbackQuery):
    hrs = int(cb.data.split(":")[1])
    rows = db.targets()
    order = {"panel": 0, "node": 1, "config": 2}
    rows.sort(key=lambda r: (order.get(r[1], 9), r[0]))
    lines = []
    for t, k in rows:
        u = db.uptime(t, hrs)
        icon = "🟢" if (u or 0) >= 99 else "🟡" if (u or 0) >= 95 else "🔴"
        lines.append(f"{icon} {e(t.split(':', 1)[-1][:40])}  <b>{u}%</b>")
    label = "۲۴ ساعت" if hrs == 24 else "۷ روز" if hrs == 168 else "۳۰ روز"
    await show(cb, f"⏱ <b>آپتایم — {label} اخیر</b>\n\n" + ("\n".join(lines) or "هنوز داده‌ای ثبت نشده؛ چند دقیقه صبر کنید."),
               kb([("۲۴ ساعت", "up:24"), ("۷ روز", "up:168"), ("۳۰ روز", "up:720")], [HOME]))


# ---------- اتصال بیش‌ازحد ----------
@router.callback_query(F.data == "ips")
async def ips(cb: CallbackQuery):
    await cb.answer("در حال بررسی…")
    snap = await fresh()
    if snap is None or not snap["panel"]["ok"]:
        return await show(cb, "🔴 اطلاعات در دسترس نیست.", kb([("🩺 عیب‌یابی", "diag")], [HOME]))
    if snap["ips_supported"] is False:
        txt = "ℹ️ نسخه‌ی پنل شما لیست IPهای آنلاین را از API نمی‌دهد، بنابراین این قابلیت فعلاً غیرفعال است."
    elif not snap["overuse"]:
        txt = f"✅ هیچ کانفیگی بیش از {db.get('max_ips')} IP هم‌زمان ندارد."
    else:
        txt = f"👥 <b>اتصال بیش‌ازحد (حد: {db.get('max_ips')} IP)</b>\n\n" + "\n\n".join(
            f"⚠️ <code>{e(o['username'])}</code> — {o['count']} IP\n<code>{e(', '.join(o['ips'][:5]))}</code>" for o in snap["overuse"])
        txt += "\n\n🛠 راه‌حل: ریست لینک اشتراک (Revoke) یا محدودیت IP برای کاربر."
    await show(cb, txt, kb([("🔄 بررسی مجدد", "ips")], [HOME]))


# ---------- لاگ‌ها ----------
@router.callback_query(F.data == "logs")
async def logs(cb: CallbackQuery):
    ev = db.events(15)
    icon = {"down": "🔴", "ok": "🟢", "warn": "🟡"}
    lines = [f"{icon.get(r['level'], '•')} <i>{datetime.fromtimestamp(r['ts']):%m-%d %H:%M}</i>  {e(r['text'])}" for r in ev]
    snap = monitor.LAST["snap"]
    errs = []
    if snap:
        errs = [f"❗️ {e(str(n.get('name')))}: <code>{e(n['message'][:150])}</code>" for n in snap["nodes"] if n.get("message")]
    txt = "📜 <b>لاگ رویدادها</b>\n\n" + ("\n".join(lines) or "رویدادی ثبت نشده.")
    if errs:
        txt += "\n\n<b>خطاهای فعلی نودها:</b>\n" + "\n".join(errs)
    await show(cb, txt[:4000], kb([("🔄 بروزرسانی", "logs")], [HOME]))


# ---------- تنظیمات ----------
FIELDS = {
    "url": ("panel_url", "🌐 آدرس پنل را بفرستید\nمثال: <code>https://panel.example.com:8000</code>"),
    "user": ("panel_user", "👤 نام‌کاربری ادمین (sudo) پنل مادر را بفرستید"),
    "pass": ("panel_pass", "🔑 رمز عبور پنل مادر را بفرستید\n<i>پیام شما بعد از دریافت پاک می‌شود.</i>"),
    "int": ("interval", "⏱ فاصله‌ی بررسی را به ثانیه بفرستید (حداقل 15)"),
    "cpu": ("cpu_th", "🔥 آستانه‌ی هشدار CPU/RAM را به درصد بفرستید (مثلاً 90)"),
    "maxip": ("max_ips", "👥 حداکثر IP هم‌زمان مجاز برای هر کانفیگ را بفرستید (مثلاً 3)"),
    "fail": ("fail_th", "🔁 بعد از چند بررسی ناموفق پشت‌سرهم اعلان قطعی ارسال شود؟ (مثلاً 2)"),
    "ipep": ("ip_endpoint", "🔌 مسیر API لیست IP آنلاین کاربر را بفرستید\nمثال: <code>/api/user/{username}/online-ips</code>"),
}


def settings_kb():
    on = lambda k: "🟢" if db.get(k) == "1" else "🔴"
    return kb(
        [("🌐 آدرس پنل", "set:url"), ("👤 نام‌کاربری", "set:user")],
        [("🔑 رمز عبور", "set:pass"), ("✅ تست اتصال", "set:test")],
        [("⏱ فاصله بررسی", "set:int"), ("🔁 حساسیت قطعی", "set:fail")],
        [("🔥 آستانه CPU/RAM", "set:cpu"), ("👥 حد IP", "set:maxip")],
        [(f"{on('notify')} اعلان‌ها", "tog:notify"), (f"{on('verify_ssl')} بررسی SSL", "tog:verify_ssl")],
        [("🔌 مسیر IP آنلاین", "set:ipep")],
        [HOME],
    )


def settings_text():
    return (
        "🛠 <b>تنظیمات</b>\n\n"
        f"🌐 آدرس: <code>{e(db.get('panel_url') or '—')}</code>\n"
        f"👤 کاربر: <code>{e(db.get('panel_user') or '—')}</code>\n"
        f"🔑 رمز: {'●●●●●●●●' if db.get('panel_pass') else '—'}\n"
        f"⏱ بررسی هر {db.get('interval')}ث | 🔁 قطعی پس از {db.get('fail_th')} بار\n"
        f"🔥 آستانه: {db.get('cpu_th')}% | 👥 حد IP: {db.get('max_ips')}"
    )


@router.callback_query(F.data == "set")
async def settings(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(cb, settings_text(), settings_kb())


@router.callback_query(F.data.startswith("tog:"))
async def toggle(cb: CallbackQuery):
    k = cb.data.split(":")[1]
    db.put(k, "0" if db.get(k) == "1" else "1")
    await show(cb, settings_text(), settings_kb())


@router.callback_query(F.data == "set:test")
async def test(cb: CallbackQuery):
    await cb.answer("در حال تست…")
    p = Panel()
    if not p.configured:
        return await cb.answer("اطلاعات ناقص است", show_alert=True)
    try:
        t = time.time()
        s = await p.system()
        await cb.answer(f"✅ اتصال موفق ({int((time.time() - t) * 1000)}ms) | نسخه {s.get('version', '?')}", show_alert=True)
    except PanelError as ex:
        await cb.answer(f"❌ {ex}", show_alert=True)


@router.callback_query(F.data.startswith("set:"))
async def ask(cb: CallbackQuery, state: FSMContext):
    f = cb.data.split(":")[1]
    if f not in FIELDS:
        return await cb.answer()
    await state.set_state(S.waiting)
    await state.update_data(field=f, mid=cb.message.message_id)
    await show(cb, FIELDS[f][1], kb([("✖️ انصراف", "set")]))


@router.message(S.waiting, F.text)
async def receive(m: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    key, _ = FIELDS[d["field"]]
    val = m.text.strip()
    if d["field"] in ("int", "cpu", "maxip", "fail"):
        if not val.isdigit() or int(val) < 1:
            return await m.answer("⛔️ یک عدد معتبر بفرستید.")
        if d["field"] == "int":
            val = str(max(15, int(val)))
    if d["field"] == "url":
        if not val.startswith("http"):
            return await m.answer("⛔️ آدرس باید با http:// یا https:// شروع شود.")
        val = val.rstrip("/")
    db.put(key, val)
    try:
        await m.delete()  # پاک‌کردن پیام (مخصوصاً رمز)
    except Exception:
        pass
    await state.clear()
    await bot.edit_message_text(settings_text(), chat_id=m.chat.id, message_id=d["mid"], reply_markup=settings_kb())


async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN or not ADMIN_IDS:
        raise SystemExit("BOT_TOKEN و ADMIN_IDS را در فایل .env تنظیم کنید.")
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.include_router(router)
    asyncio.create_task(monitor.loop(bot))
    await dp.start_polling(bot)
