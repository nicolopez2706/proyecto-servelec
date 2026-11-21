"""Cabeceras de seguridad para todas las respuestas del portal (ASGI puro: no depende de FastAPI).
- El portal no se puede meter dentro de otra página (evita el «clickjacking»).
- El navegador no adivina tipos de archivo y no manda de dónde vino el enlace.
- Solo se cargan recursos del propio portal (la pantalla es un único archivo con estilos y script adentro).
- Las respuestas de la API no se guardan en el caché del navegador (traen datos de pedidos y usuarios)."""

CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
BASE = [(b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"), (b"referrer-policy", b"no-referrer"),
        (b"content-security-policy", CSP.encode())]


class CabecerasSeguras:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        es_api = scope.get("path", "").startswith("/api/")

        async def enviar(msg):
            if msg["type"] == "http.response.start":
                puestas = {k.lower() for k, _ in msg.get("headers", [])}
                extra = [h for h in BASE if h[0] not in puestas]
                if es_api and b"cache-control" not in puestas:
                    extra.append((b"cache-control", b"no-store"))
                msg = dict(msg, headers=list(msg.get("headers", [])) + extra)
            await send(msg)

        await self.app(scope, receive, enviar)
