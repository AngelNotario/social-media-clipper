"""Punto de entrada del backend de SOCIAL MEDIA CLIPPER."""
from __future__ import annotations

import logging
import warnings
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, jobs
from .routers import ai, browse, knowledge, library, publish, resources, stream, studio, system, thumbs


# Ruido inofensivo en la consola: aviso de SOCKS de urllib3 (no usamos proxies SOCKS) y el
# ConnectionResetError de asyncio en Windows cuando el navegador cierra una conexión.
warnings.filterwarnings("ignore", message=".*SOCKS support in urllib3.*")


class _QuietReset(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        return not (isinstance(exc, ConnectionResetError) or "_call_connection_lost" in record.getMessage())


logging.getLogger("asyncio").addFilter(_QuietReset())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    from .studio import footage, learn
    footage.ensure_schema()
    learn.ensure_schema()
    from . import stream as stream_mod
    stream_mod.ensure_schema()
    from . import knowledge as knowledge_mod
    knowledge_mod.ensure_schema()
    from . import taste as taste_mod
    taste_mod.ensure_schema()
    jobs.start_worker()
    from .publish import runner as publish_runner
    publish_runner.start()
    from .ai import llamacpp_server
    llamacpp_server.autostart()  # si el motor de IA elegido es llama.cpp, se abre solo
    yield
    llamacpp_server.stop()  # solo cierra el servidor que abrió la app


app = FastAPI(title="SOCIAL MEDIA CLIPPER", version="0.1.0", lifespan=lifespan)

# El frontend en modo desarrollo (Vite) corre en el puerto 5173
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (system.router, browse.router, ai.router, studio.router, library.router, stream.router, knowledge.router,
          publish.router, thumbs.router, resources.router):
    app.include_router(r, prefix="/api")


# Sirve el frontend compilado (frontend/dist) desde el mismo puerto
if config.FRONTEND_DIST.is_dir():
    assets = config.FRONTEND_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "No encontrado"}, status_code=404)
        candidate = (config.FRONTEND_DIST / full_path).resolve()
        if full_path and candidate.is_file() and config.FRONTEND_DIST in candidate.parents:
            return FileResponse(candidate)
        # index.html sin caché: así el navegador recibe la versión nueva tras cada actualización
        return FileResponse(config.FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-cache"})
else:
    @app.get("/", include_in_schema=False)
    def no_frontend():
        return JSONResponse({
            "message": "Backend activo. El frontend no está compilado: ejecuta 'npm run build' en /frontend.",
            "docs": "/docs",
        })
