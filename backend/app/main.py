"""Servidor del portal. Desde la carpeta backend:   uvicorn app.main:app --reload
Abre http://localhost:8000  (la pantalla del portal). Toda la logica esta en router.py y services.py."""
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from . import engine, services
from .config import settings
from .db import init_db
from .router import dispatch

STATIC = Path(__file__).resolve().parent / "static"


def _calentar():
    """Al arrancar, lee el indice y recorre las carpetas en segundo plano: asi la primera busqueda ya es rapida."""
    import time
    t0 = time.monotonic()
    try:
        if settings.docs_root.exists():
            engine.system_warnings(settings.docs_root, wait=False)
            data, _ = engine.scan(settings.docs_root, settings.scan_ttl or 30)
            print(f"[portal] indice listo: {len(data)} planillas en {time.monotonic() - t0:.1f}s", flush=True)
    except Exception as e:
        print(f"[portal] no se pudo preparar el indice: {e!r}", flush=True)


@asynccontextmanager
async def lifespan(app):
    init_db()
    settings.secret_key                                  # la crea ahora si hace falta (y no en el primer ingreso)
    if settings.secret_key_origin == "archivo":
        print(f"[portal] SECRET_KEY no esta en .env: se usa la clave propia guardada en {settings.data_dir / 'secret.key'}", flush=True)
    try:
        for oid in services.recover_interrupted():
            print(f"[portal] el pedido #{oid} había quedado a medias y se recuperó", flush=True)
    except Exception as e:
        print(f"[portal] no se pudo revisar pedidos interrumpidos: {e!r}", flush=True)
    threading.Thread(target=_calentar, daemon=True, name="calentar-indice").start()
    yield


app = FastAPI(title="Portal de documentación SERVELEC", version="0.2", lifespan=lifespan)


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PATCH", "DELETE"])
async def api(rest: str, request: Request):
    body = {}
    if request.method in ("POST", "PATCH"):
        try:
            body = await request.json()
        except Exception:
            body = {}
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else None
    # En un hilo aparte: si un pedido tarda (recorrer carpetas, LibreOffice), el resto del portal sigue respondiendo.
    t0 = time.monotonic()
    status, data = await run_in_threadpool(dispatch, request.method, "/api/" + rest, dict(request.query_params), body, token)
    if time.monotonic() - t0 > 2:
        print(f"[portal] pedido lento: {request.method} /api/{rest} tardo {time.monotonic() - t0:.1f}s", flush=True)
    return JSONResponse(data, status_code=status)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/static/{name}")
def static_file(name: str):
    f = STATIC / Path(name).name
    return FileResponse(f) if f.exists() else JSONResponse({"detail": "No existe."}, status_code=404)
