"""Minimal Zetaris REST client: log in once, run Lightning SQL, return rows as dicts."""
import os
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv()

API = os.getenv("ZETARIS_URL", "http://localhost:8888/api/v1.0").rstrip("/")
UI = os.getenv("ZETARIS_UI_URL", "http://localhost:3000").rstrip("/")
USL = "lightning.metastore.dd.dd_usl"  # namespace.usl the agent queries


class ZetarisError(RuntimeError):
    pass


class Zetaris:
    def __init__(self):
        self.headers = None

    def _login(self):
        password = os.getenv("ZETARIS_PASSWORD")
        if not password:
            raise ZetarisError("Set ZETARIS_PASSWORD in .env")
        r = httpx.post(f"{UI}/api/auth/login", timeout=30,
                       json={"username": os.getenv("ZETARIS_USERNAME", "admin@zetaris.com"), "password": password})
        if r.status_code != 200 or "idToken" not in r.text:
            raise ZetarisError(f"login failed: HTTP {r.status_code}")
        body = r.json()
        self.headers = {"Authorization": f"Bearer {body['idToken']}", "X-Org-ID": str(body["orgId"])}

    def _payload(self, sql: str, limit: int):
        if not self.headers:
            self._login()
        return dict(url=f"{API}/sql-editor/sqls/run",
                    headers={**self.headers, "X-Request-ID": str(uuid.uuid4())},
                    json={"queryId": str(uuid.uuid4()), "sql": sql, "source": "SqlEditor", "limit": limit})

    @staticmethod
    def _rows(r: httpx.Response) -> list[dict]:
        if r.status_code != 200:
            raise ZetarisError(f"HTTP {r.status_code}: {r.text[:300]}")
        body = r.json()
        return [dict(zip(body.get("headers", []), row)) for row in body.get("data") or []]

    def query(self, sql: str, limit: int = 200) -> list[dict]:
        return self._rows(httpx.post(**self._payload(sql, limit), timeout=600))

    async def aquery(self, sql: str, limit: int = 200) -> list[dict]:
        async with httpx.AsyncClient(timeout=600) as http:
            return self._rows(await http.post(**self._payload(sql, limit)))
