"""Puerta con contraseña delante de la app para el túnel (no toca el servidor de la app).

Uso: python proxy.py  -> escucha en 127.0.0.1:8010 y reenvía a http://127.0.0.1:8000
Usuario/contraseña en tunnel_pass.txt (línea "usuario:contraseña").
"""
import base64
import secrets
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

HERE = Path(__file__).parent
USER, PWD = (HERE / "tunnel_pass.txt").read_text(encoding="utf-8").strip().split(":", 1)
TARGET = "http://127.0.0.1:8000"
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length", "authorization"}
client = httpx.AsyncClient(base_url=TARGET, timeout=httpx.Timeout(None, connect=10))


def _ok(req: Request) -> bool:
    h = req.headers.get("authorization", "")
    if not h.startswith("Basic "):
        return False
    try:
        u, p = base64.b64decode(h[6:]).decode().split(":", 1)
    except Exception:
        return False
    return secrets.compare_digest(u, USER) and secrets.compare_digest(p, PWD)


async def proxy(req: Request):
    if not _ok(req):
        return Response("Contraseña requerida", 401, {"WWW-Authenticate": 'Basic realm="Social Media Clipper"'})
    headers = {k: v for k, v in req.headers.items() if k.lower() not in HOP}
    up = client.build_request(req.method, req.url.path, params=req.url.query, headers=headers,
                              content=req.stream() if req.method not in ("GET", "HEAD") else None)
    r = await client.send(up, stream=True)
    out = {k: v for k, v in r.headers.items() if k.lower() not in HOP}
    return StreamingResponse(r.aiter_raw(), status_code=r.status_code, headers=out, background=BackgroundTask(r.aclose))


app = Starlette(routes=[Route("/{path:path}", proxy, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])])

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8010, log_level="warning")
