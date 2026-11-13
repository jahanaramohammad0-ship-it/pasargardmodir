"""کلاینت ساده‌ی API پنل پاسارگارد (لاگین با نام‌کاربری/رمز پنل مادر)."""
import asyncio

import aiohttp

from . import db


class PanelError(Exception):
    """kind: config | network | timeout | ssl | auth | notfound | server | http"""

    def __init__(self, kind, msg):
        super().__init__(msg)
        self.kind = kind


_tok = {"key": None, "val": None}


def as_list(data, *keys):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return data[k]
    return []


class Panel:
    def __init__(self):
        self.url = (db.get("panel_url") or "").rstrip("/")
        self.user = db.get("panel_user") or ""
        self.pw = db.get("panel_pass") or ""
        self.verify = db.get("verify_ssl") == "1"

    @property
    def configured(self):
        return bool(self.url and self.user and self.pw)

    def _session(self):
        return aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            connector=aiohttp.TCPConnector(ssl=None if self.verify else False),
        )

    async def _login(self, s):
        try:
            async with s.post(f"{self.url}/api/admin/token", data={"username": self.user, "password": self.pw}) as r:
                if r.status in (401, 403, 422):
                    raise PanelError("auth", "نام‌کاربری یا رمز پنل اشتباه است (یا ادمین sudo نیست)")
                if r.status >= 400:
                    raise PanelError("http", f"لاگین ناموفق بود (HTTP {r.status})")
                tok = (await r.json()).get("access_token")
                if not tok:
                    raise PanelError("auth", "توکنی از پنل دریافت نشد")
                _tok.update(key=(self.url, self.user, self.pw), val=tok)
                return tok
        except PanelError:
            raise
        except Exception as e:  # noqa
            raise self._wrap(e)

    @staticmethod
    def _wrap(e):
        if isinstance(e, asyncio.TimeoutError):
            return PanelError("timeout", "پنل پاسخ نداد (Timeout)")
        if isinstance(e, aiohttp.ClientSSLError) or "SSL" in type(e).__name__ or "certificate" in str(e).lower():
            return PanelError("ssl", f"خطای SSL/گواهی: {e}")
        if isinstance(e, aiohttp.ClientError):
            return PanelError("network", f"عدم اتصال به پنل: {e}")
        return PanelError("http", str(e))

    async def req(self, method, path, **kw):
        if not self.configured:
            raise PanelError("config", "آدرس/نام‌کاربری/رمز پنل در تنظیمات وارد نشده")
        async with self._session() as s:
            key = (self.url, self.user, self.pw)
            tok = _tok["val"] if _tok["key"] == key else await self._login(s)
            for attempt in (1, 2):
                try:
                    async with s.request(method, f"{self.url}{path}", headers={"Authorization": f"Bearer {tok}"}, **kw) as r:
                        if r.status == 401 and attempt == 1:
                            tok = await self._login(s)
                            continue
                        if r.status == 404:
                            raise PanelError("notfound", f"{path} پیدا نشد (HTTP 404)")
                        if r.status >= 500:
                            raise PanelError("server", f"خطای داخلی پنل (HTTP {r.status})")
                        if r.status >= 400:
                            raise PanelError("http", f"HTTP {r.status} روی {path}")
                        try:
                            return await r.json()
                        except Exception:
                            return {}
                except PanelError:
                    raise
                except Exception as e:  # noqa
                    raise self._wrap(e)

    # ---- منابع ----
    async def system(self):
        return await self.req("GET", "/api/system")

    async def nodes(self):
        return as_list(await self.req("GET", "/api/nodes"), "nodes", "items")

    async def hosts(self):
        return as_list(await self.req("GET", "/api/hosts"), "hosts", "items")

    async def users(self, status="active", limit=200):
        d = await self.req("GET", "/api/users", params={"status": status, "limit": limit, "offset": 0})
        return as_list(d, "users", "items")

    async def user_ips(self, username):
        """لیست IPهای آنلاین یک کاربر؛ اگر نسخه‌ی پنل پشتیبانی نکند None برمی‌گرداند."""
        tpl = db.get("ip_endpoint") or "/api/user/{username}/online-ips"
        try:
            d = await self.req("GET", tpl.format(username=username))
        except PanelError as e:
            if e.kind == "notfound":
                return None
            raise
        if isinstance(d, dict):
            d = d.get("ips") or d.get("online_ips") or d.get("data") or []
        return list(d) if isinstance(d, (list, dict)) else []

    async def node_reconnect(self, node_id):
        last = None
        for path in (f"/api/node/{node_id}/reconnect", f"/api/nodes/{node_id}/reconnect"):
            try:
                return await self.req("POST", path)
            except PanelError as e:
                last = e
                if e.kind != "notfound":
                    break
        raise last
