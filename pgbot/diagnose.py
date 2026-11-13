"""موتور عیب‌یابی: تبدیل وضعیت پنل به لیست مشکل‌ها + راه‌حل."""
from dataclasses import dataclass

from . import db


@dataclass
class Issue:
    level: str   # crit | warn | info
    title: str
    detail: str
    fix: str


ICON = {"crit": "🔴", "warn": "🟡", "info": "🔵"}

PANEL_FIX = {
    "network": ("پنل در دسترس نیست",
                "۱) روی سرور پنل بزنید: <code>pasarguard status</code> و در صورت نیاز <code>pasarguard restart</code>\n"
                "۲) لاگ‌ها: <code>pasarguard logs</code>\n"
                "۳) پورت پنل در فایروال باز باشد (<code>ufw status</code>)\n"
                "۴) آدرس/پورت در تنظیمات ربات را چک کنید (https:// و پورت درست)."),
    "timeout": ("پنل جواب نمی‌دهد (Timeout)",
                "سرور احتمالاً زیر بار شدید است یا فایروال/فیلترینگ مسیر را کند کرده. "
                "<code>htop</code> و <code>pasarguard logs</code> را ببینید؛ دیتابیس حجیم یا RAM کم رایج‌ترین علت است."),
    "ssl": ("خطای گواهی SSL پنل",
            "گواهی منقضی یا نامعتبر است. تمدید: <code>certbot renew</code> یا گواهی را جایگزین کنید. "
            "اگر self-signed است، در تنظیمات ربات «بررسی SSL» را خاموش کنید."),
    "auth": ("ورود به پنل ناموفق",
             "نام‌کاربری/رمز در تنظیمات ربات اشتباه است یا ادمین sudo نیست. "
             "رمز را با <code>pasarguard cli admin reset</code> بازنشانی کنید و دوباره در تنظیمات وارد کنید."),
    "server": ("خطای 5xx از پنل",
               "پنل بالا آمده ولی خطای داخلی می‌دهد. <code>pasarguard logs</code> را بخوانید؛ اغلب دیتابیس یا کانفیگ Xray خراب است."),
    "notfound": ("مسیر API پیدا نشد",
                 "نسخه‌ی پنل با ربات سازگار نیست یا آدرس پایه اشتباه است (نباید /dashboard داشته باشد). پنل را به‌روزرسانی کنید."),
    "config": ("اطلاعات پنل وارد نشده", "از منوی «تنظیمات» آدرس، نام‌کاربری و رمز پنل مادر را وارد کنید."),
    "http": ("پاسخ نامعتبر از پنل", "آدرس پنل و ورژن آن را بررسی کنید."),
}


def node_fix(msg: str):
    m = (msg or "").lower()
    if "timeout" in m or "timed out" in m:
        return ("پورت‌های نود (پیش‌فرض 62050 و 62051) از سمت پنل بسته است یا سرور نود فیلتر/کند است. "
                "در فایروال نود، IP سرور پنل را Allow کنید: <code>ufw allow from IP_پنل to any port 62050,62051 proto tcp</code>")
    if "refused" in m or "unreachable" in m or "no route" in m:
        return "سرویس نود بالا نیست. روی نود: <code>pasarguard-node status</code> / <code>restart</code> (یا docker compose restart) و بررسی پورت."
    if "ssl" in m or "certificate" in m or "handshake" in m:
        return "گواهی نود با پنل نمی‌خواند. گواهی (Certificate) را از پنل دوباره کپی کنید در نود و نود را ری‌استارت کنید."
    if "version" in m or "core" in m or "xray" in m:
        return "ناسازگاری نسخه‌ی Xray/نود. از بخش Core پنل نسخه‌ی Xray را یکی کنید و نود را به‌روز کنید."
    if "config" in m or "json" in m:
        return "کانفیگ Xray خطا دارد. در Core Settings پنل JSON را اعتبارسنجی کنید یا به آخرین نسخه‌ی سالم برگردید."
    return "در پنل روی نود «Reconnect» بزنید (دکمه‌ی زیر)، اگر ماند لاگ نود را بخوانید: <code>pasarguard-node logs</code>."


def diagnose(snap) -> list[Issue]:
    out: list[Issue] = []
    if not snap:
        return [Issue("info", "هنوز بررسی انجام نشده", "", "چند لحظه صبر کنید یا «بررسی مجدد» بزنید.")]
    p = snap["panel"]
    if not p["ok"]:
        t, f = PANEL_FIX.get(p.get("kind"), PANEL_FIX["http"])
        return [Issue("crit", t, p.get("msg", ""), f)]

    s = snap["system"]
    th = float(db.get("cpu_th"))
    cpu = s.get("cpu_usage")
    if isinstance(cpu, (int, float)) and cpu >= th:
        out.append(Issue("warn", f"مصرف CPU بالا ({cpu:.0f}%)", "", "پردازش سنگین را پیدا کنید (<code>htop</code>)؛ تعداد کاربر/نود زیاد یا لاگ‌گیری زیاد دلیل رایج است. ارتقای سرور را بسنجید."))
    mt, mu = s.get("mem_total"), s.get("mem_used")
    if mt and mu and mu / mt * 100 >= th:
        out.append(Issue("warn", f"مصرف RAM بالا ({mu / mt * 100:.0f}%)", "", "Swap اضافه کنید یا RAM را بیشتر کنید؛ در غیر این صورت پنل با OOM kill می‌افتد."))
    if p.get("ms", 0) > 3000:
        out.append(Issue("warn", f"پاسخ‌دهی کند پنل ({p['ms']}ms)", "", "دیتابیس را بهینه کنید (مهاجرت از SQLite به MySQL/PostgreSQL برای کاربر زیاد) و منابع سرور را بررسی کنید."))

    for n in snap["nodes"]:
        st = n.get("status")
        if st == "connected":
            if n.get("tcp_ms") and n["tcp_ms"] > 800:
                out.append(Issue("info", f"تأخیر بالا تا نود {n.get('name')} ({n['tcp_ms']}ms)", n.get("location", ""), "مسیر شبکه بین پنل و نود ضعیف است؛ دیتاسنتر/مسیر نزدیک‌تر را بسنجید."))
            continue
        if st == "disabled":
            continue
        if st == "connecting":
            out.append(Issue("warn", f"نود {n.get('name')} در حال اتصال است", n.get("message") or "", node_fix(n.get("message"))))
        else:
            out.append(Issue("crit", f"نود {n.get('name')} خطا دارد ({st})", n.get("message") or "", node_fix(n.get("message"))))

    for h in snap["hosts"]:
        if h["tcp_ms"] is None:
            out.append(Issue("crit", f"کانفیگ «{h['remark']}» در دسترس نیست", f"{h['address']}:{h['port']}",
                             "پورت از سمت ربات قابل اتصال نیست: ۱) اینباند روی نود/پنل فعال و listen باشد ۲) پورت در فایروال باز باشد "
                             "۳) IP یا دامنه احتمالاً فیلتر شده → IP/دامنه را عوض کنید یا از CDN/پورت دیگر استفاده کنید."))
    for o in snap["overuse"]:
        out.append(Issue("warn", f"اتصال بیش‌ازحد: {o['username']} ({o['count']} IP)", ", ".join(o["ips"][:5]),
                         "احتمالاً لینک اشتراک‌گذاری شده است. کاربر را محدود/ریست لینک (Revoke Subscription) کنید یا IP limit تنظیم کنید."))
    if snap.get("ips_supported") is False:
        out.append(Issue("info", "تشخیص اتصال بیش‌ازحد غیرفعال است", "نسخه‌ی پنل لیست IP آنلاین را از API نمی‌دهد",
                         "اگر endpoint دیگری دارد در دیتابیس کلید <code>ip_endpoint</code> را تنظیم کنید (مثلاً /api/user/{username}/online-ips)."))
    for e in snap["errors"]:
        out.append(Issue("warn", "خطا در دریافت اطلاعات", e, "دسترسی ادمین (sudo) و نسخه‌ی پنل را بررسی کنید."))
    if not out:
        out.append(Issue("info", "همه‌چیز سالم است ✅", "", "مشکلی پیدا نشد."))
    return out
