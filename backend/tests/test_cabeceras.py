"""Cabeceras de seguridad (se prueba el middleware ASGI directamente, sin FastAPI)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.cabeceras import CabecerasSeguras  # noqa: E402


def _llamar(path, headers=()):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": list(headers)})
        await send({"type": "http.response.body", "body": b"ok"})

    salida = []

    async def send(m):
        salida.append(m)

    asyncio.run(CabecerasSeguras(app)({"type": "http", "path": path}, None, send))
    return {k: v for k, v in salida[0]["headers"]}, salida[1]["body"]


def test_la_pantalla_lleva_las_cabeceras():
    h, body = _llamar("/")
    assert body == b"ok"
    assert h[b"x-frame-options"] == b"DENY" and h[b"x-content-type-options"] == b"nosniff"
    assert b"frame-ancestors 'none'" in h[b"content-security-policy"] and b"cache-control" not in h


def test_la_api_no_se_guarda_en_cache():
    h, _ = _llamar("/api/orders")
    assert h[b"cache-control"] == b"no-store"


def test_no_pisa_lo_que_ya_puso_la_app():
    h, _ = _llamar("/api/x", headers=[(b"Cache-Control", b"max-age=5"), (b"X-Frame-Options", b"SAMEORIGIN")])
    assert h[b"Cache-Control"] == b"max-age=5" and h[b"X-Frame-Options"] == b"SAMEORIGIN"
    assert b"cache-control" not in h and b"x-frame-options" not in h


def test_lo_que_no_es_http_pasa_sin_tocar():
    llamado = []

    async def app(scope, receive, send):
        llamado.append(scope["type"])

    asyncio.run(CabecerasSeguras(app)({"type": "lifespan"}, None, None))
    assert llamado == ["lifespan"]
