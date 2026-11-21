"""Servidor de prueba SIN FastAPI (solo biblioteca estandar) que usa la misma tabla de rutas.
Sirve para probar la pantalla en un entorno sin paquetes. Uso: python tests/devserver.py [puerto]"""
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.db import init_db  # noqa: E402
from app.router import dispatch  # noqa: E402

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


class H(BaseHTTPRequestHandler):
    def _send(self, status, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status); self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        from app.cabeceras import BASE
        for k, v in BASE:                                    # mismas cabeceras que en el portal real (para probar que la pantalla las soporta)
            self.send_header(k.decode(), v.decode())
        self.end_headers(); self.wfile.write(data)

    def _go(self, method):
        u = urlparse(self.path)
        if method == "GET" and (u.path == "/" or u.path.startswith("/static/")):
            f = STATIC / ("index.html" if u.path == "/" else Path(u.path).name)
            return self._send(200, f.read_bytes(), "image/png" if f.suffix == ".png" else "text/html") if f.exists() else self._send(404, {"detail": "no"})
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        auth = self.headers.get("Authorization", "")
        st, data = dispatch(method, unquote(u.path), dict(parse_qsl(u.query)), body, auth[7:] if auth.lower().startswith("bearer ") else None)
        self._send(st, data)

    do_GET = lambda s: s._go("GET")
    do_POST = lambda s: s._go("POST")
    do_PATCH = lambda s: s._go("PATCH")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    init_db()
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8000), H).serve_forever()
