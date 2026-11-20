import json
from test_core import env  # noqa: F401
from app.router import dispatch
from app.security import make_token


def call(method, path, token=None, body=None, query=None):
    return dispatch(method, path, query, body, token)


def login(who="ing@x"):
    st, d = call("POST", "/api/login", body={"email": who, "password": "clave1234"})
    assert st == 200
    return d["token"]


def test_login_y_permisos_http(env):
    assert call("POST", "/api/login", body={"email": "ing@x", "password": "mala"})[0] == 401
    assert call("GET", "/api/me")[0] == 401
    assert call("GET", "/api/me", token="basura")[0] == 401
    t = login("ab@x")
    st, d = call("GET", "/api/me", t); assert st == 200 and d["user"]["role"] == "Abastecimiento" and "approve" not in d["perms"]
    assert call("GET", "/api/audit", t)[0] == 403
    assert call("GET", "/api/users", t)[0] == 403
    assert call("GET", "/api/nada", t)[0] == 404
    assert call("DELETE", "/api/me", t)[0] == 405


def test_flujo_http_completo(env):
    ing, ger, ab = login("ing@x"), login("ger@x"), login("ab@x")
    st, pv = call("POST", "/api/preview", ing, {"from": "DIS206AA", "to": "DIS206BA", "qty": {"on": True, "factor": 2}})
    assert st == 200 and pv["sim"]["total_cells"] == 4
    st, o = call("POST", "/api/orders", ing, {"type": "codigo", "params": {"from": "DIS206AA", "to": "DIS206BA", "rev_mode": "rev"}})
    assert st == 200 and o["status"] == "pendiente"
    assert call("POST", f"/api/orders/{o['id']}/approve", ab, {})[0] == 403
    assert call("POST", f"/api/orders/{o['id']}/approve", ger, {"folders": ["NO/EXISTE"]})[0] == 400
    st, r = call("POST", f"/api/orders/{o['id']}/approve", ger, {"comment": "ok", "folders": [f for f in o["sim"]["folders"]][:2]})
    assert st == 200 and r["status"] == "parcial" and r["result"]["changes"] == 3
    assert call("GET", f"/api/orders/{o['id']}", ab)[1]["id"] == o["id"]
    assert len(call("GET", "/api/orders", ab, query={"status": "parcial"})[1]) == 1
    assert call("POST", f"/api/orders/{o['id']}/revert", ger)[1]["status"] == "revertido"
    assert call("GET", "/api/orders/999", ab)[0] == 404


def test_catalogo_http(env):
    ab, ing = login("ab@x"), login("ing@x")
    st, r = call("POST", "/api/requests", ab, {"what": "falta el tornillo M8 inox"}); assert st == 200
    assert call("POST", "/api/requests", ab, {"what": "x"})[0] == 400
    assert call("POST", "/api/codes", ab, {"code": "T1", "desc": "x"})[0] == 403
    st, c = call("POST", "/api/codes", ing, {"code": "tor-m8", "desc": "Tornillo M8 inox", "kind": "insumo", "request_id": r["id"]})
    assert st == 200 and c["code"] == "TOR-M8"
    assert call("GET", "/api/codes", ab, query={"q": "tornillo"})[1][0]["code"] == "TOR-M8"
    assert call("GET", "/api/codes/DIS206AA", ab)[1]["cells"] == 4
    assert call("GET", "/api/codes/NOEXISTE", ab)[0] == 404
    assert call("GET", "/api/revisions", ing)[0] == 200 and call("GET", "/api/revisions", ab)[0] == 403
    assert call("POST", "/api/cleanup", ing, {"dry_run": True})[1]["dry_run"] is True


def test_health_solo_sistemas(env):
    sis = login("sis@x")
    st, d = call("GET", "/api/health", sis)
    assert st == 200 and "libreoffice" in d and ".ods" in d["formatos"]
    assert call("GET", "/api/health", login("ger@x"))[0] == 403
