"""Rutas de la API sin depender de ningun framework: dispatch(metodo, ruta, query, cuerpo, token).
FastAPI (main.py) y el servidor de prueba usan esta misma tabla."""
import re
import traceback

from . import engine, services
from .config import settings
from .permissions import PERMS, PermissionDenied, require
from .security import make_token, read_token


class HttpError(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail


def _perms(u):
    return [p for p, roles in PERMS.items() if u["role"] in roles]


def _login(u, m, q, b):
    user = services.authenticate(b.get("email", ""), b.get("password", ""))
    if not user:
        raise HttpError(401, "Correo o clave incorrectos.")
    return {"token": make_token(user["email"]), "user": user, "perms": _perms(user), "read_only": settings.read_only}


def _health(u, m, q, b):
    require(u, "admin")
    exe = engine.find_soffice()
    return {"docs_root": str(settings.docs_root), "docs_root_exists": settings.docs_root.exists(),
            "data_dir": str(settings.data_dir), "libreoffice": exe, "read_only": settings.read_only, "formatos": sorted(engine.EXTS)}


PUBLIC = {("POST", "/api/login")}

ROUTES = [
    ("POST", r"/api/login", _login),
    ("GET", r"/api/me", lambda u, m, q, b: {"user": u, "perms": _perms(u), "read_only": settings.read_only, "warnings": engine.system_warnings(settings.docs_root, wait=False) if _perms(u) else []}),
    ("POST", r"/api/me/password", lambda u, m, q, b: services.change_password(u, b.get("current", ""), b.get("new", ""))),
    ("GET", r"/api/health", _health),
    ("GET", r"/api/people", lambda u, m, q, b: services.people(u)),
    ("GET", r"/api/codes", lambda u, m, q, b: services.catalog_list(u, q.get("q", ""))),
    ("POST", r"/api/codes", lambda u, m, q, b: services.generate_code(u, b.get("code"), b.get("desc"), b.get("kind", ""), b.get("alts"), b.get("request_id"))),
    ("GET", r"/api/codes/([^/]+)", lambda u, m, q, b: services.code_info(u, m[0])),
    ("GET", r"/api/requests", lambda u, m, q, b: services.list_requests(u, q.get("status"))),
    ("POST", r"/api/requests", lambda u, m, q, b: services.request_code(u, b.get("what"), b.get("kind", ""))),
    ("POST", r"/api/requests/(\d+)/reject", lambda u, m, q, b: services.reject_request(u, int(m[0]), b.get("note", "")) or {"ok": True}),
    ("POST", r"/api/preview", lambda u, m, q, b: services.preview(u, b)),
    ("POST", r"/api/orders", lambda u, m, q, b: services.create_order(u, b.get("type", "codigo"), b.get("params", {}))),
    ("GET", r"/api/orders", lambda u, m, q, b: services.list_orders(u, q.get("status"))),
    ("GET", r"/api/orders/(\d+)", lambda u, m, q, b: services.get_order(u, int(m[0]))),
    ("POST", r"/api/orders/(\d+)/approve", lambda u, m, q, b: services.approve(u, int(m[0]), b.get("comment", ""), b.get("folders"))),
    ("POST", r"/api/orders/(\d+)/reject", lambda u, m, q, b: services.reject(u, int(m[0]), b.get("comment", ""))),
    ("POST", r"/api/orders/(\d+)/cancel", lambda u, m, q, b: services.cancel(u, int(m[0]))),
    ("POST", r"/api/orders/(\d+)/revert", lambda u, m, q, b: services.revert(u, int(m[0]))),
    ("GET", r"/api/conv", lambda u, m, q, b: services.list_conv(u)),
    ("GET", r"/api/conv/([^/]+)/([^/]+)", lambda u, m, q, b: services.get_conv(u, m[0], m[1])),
    ("GET", r"/api/search", lambda u, m, q, b: services.search(u, q.get("q", ""))),
    ("GET", r"/api/revisions", lambda u, m, q, b: services.revision_status(u)),
    ("POST", r"/api/cleanup", lambda u, m, q, b: services.run_cleanup(u, bool(b.get("dry_run", True)))),
    ("GET", r"/api/links", lambda u, m, q, b: services.links_report(u)),
    ("POST", r"/api/links/repair", lambda u, m, q, b: services.links_repair(u, bool(b.get("dry_run", True)))),
    ("GET", r"/api/changes", lambda u, m, q, b: services.changes_log(u, q.get("q", ""))),
    ("GET", r"/api/audit", lambda u, m, q, b: services.list_audit(u)),
    ("GET", r"/api/users", lambda u, m, q, b: services.list_users(u)),
    ("POST", r"/api/users", lambda u, m, q, b: services.create_user(u, b.get("email", ""), b.get("name", ""), b.get("role", ""), b.get("password", "")) or {"ok": True}),
    ("PATCH", r"/api/users/([^/]+)", lambda u, m, q, b: services.set_role(u, m[0], b.get("role"), b.get("active")) or {"ok": True}),
]
_COMPILED = [(meth, re.compile("^" + pat + "$"), fn) for meth, pat, fn in ROUTES]


def dispatch(method, path, query=None, body=None, token=None):
    """Devuelve (status_http, objeto_json)."""
    query, body = query or {}, body or {}
    path = path.rstrip("/") or "/"
    try:
        matched = None
        for meth, rx, fn in _COMPILED:
            m = rx.match(path)
            if m and meth == method:
                matched = (fn, m.groups())
                break
            if m:
                matched = matched or "method"
        if matched is None:
            raise HttpError(404, "Ruta inexistente.")
        if matched == "method":
            raise HttpError(405, "Método no permitido.")
        fn, groups = matched
        user = None
        if (method, path) not in PUBLIC:
            email = read_token(token) if token else None
            user = services.get_user(email) if email else None
            if not user:
                raise HttpError(401, "Ingresá de nuevo.")
        return 200, fn(user, groups, query, body)
    except HttpError as e:
        return e.status, {"detail": e.detail}
    except PermissionDenied as e:
        return 403, {"detail": str(e)}
    except LookupError as e:
        return 404, {"detail": str(e)}
    except ValueError as e:
        return 400, {"detail": str(e)}
    except Exception as e:  # error inesperado: se informa sin tirar el servidor
        traceback.print_exc()
        return 500, {"detail": f"Error interno: {type(e).__name__}: {e}"}
