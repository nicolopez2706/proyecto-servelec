import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from test_core import env, pedir  # noqa: F401
from app import events
from app.permissions import PermissionDenied, SERVICE_ROLE


@pytest.fixture()
def n8n(monkeypatch):
    """Un 'n8n' falso: guarda lo que le llega."""
    got = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            got.append({"headers": dict(self.headers), "body": json.loads(body)})
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("N8N_WEBHOOK_URL", f"http://127.0.0.1:{srv.server_port}/webhook/portal-eventos")
    monkeypatch.setenv("N8N_WEBHOOK_SECRET", "clave-compartida")
    monkeypatch.setenv("N8N_DELAY", "0")
    yield got
    srv.shutdown()


def _esperar(got, n, seg=5):
    t = time.time()
    while len(got) < n and time.time() - t < seg:
        time.sleep(0.05)


def test_sin_url_no_avisa_ni_falla(env, monkeypatch):
    services, u, docs, _ = env
    monkeypatch.delenv("N8N_WEBHOOK_URL", raising=False)
    assert events.emit("Creó pedido", "#1") is False
    pedir(services, u)                                       # no debe romper nada


def test_los_pedidos_avisan_a_n8n_con_resumen(env, n8n):
    services, u, docs, _ = env
    o = pedir(services, u)
    services.approve(u["ger@x"], o["id"])
    _esperar(n8n, 3)
    por_evento = {g["body"]["event"]: g for g in n8n}
    assert {"pedido_creado", "pedido_aprobado", "pedido_ejecutado"} <= set(por_evento)
    c = por_evento["pedido_creado"]
    assert c["headers"]["X-Portal-Token"] == "clave-compartida" and c["headers"]["X-Portal-Event"] == "pedido_creado"
    ped = c["body"]["order"]
    assert ped["id"] == o["id"] and ped["from"] == "DIS206AA" and ped["to"] == "DIS206BA"
    assert ped["requested_by"] == "Luca" and ped["planned"]["cells"] >= 1
    assert ped["url"].endswith(f"/#/pedidos/{o['id']}")
    fin = por_evento["pedido_ejecutado"]["body"]["order"]
    assert fin["decided_by"] == "Priscila" and fin["result"]["changes"] >= 1


def test_acciones_que_no_son_pedidos_no_avisan(env, n8n):
    services, u, docs, _ = env
    services.change_password(u["ab@x"], "clave1234", "nuevaclave1")      # "Cambió su clave" no se informa
    time.sleep(0.5)
    assert n8n == []


def test_si_n8n_no_responde_el_portal_sigue(env, monkeypatch):
    services, u, docs, _ = env
    monkeypatch.setenv("N8N_WEBHOOK_URL", "http://127.0.0.1:9/nada")     # puerto cerrado
    monkeypatch.setattr(events, "ATTEMPTS", (0,))
    t = time.time()
    o = pedir(services, u)
    assert o["status"] == "pendiente" and time.time() - t < 5            # el aviso va en segundo plano


def test_aviso_de_prueba(env, n8n, monkeypatch):
    services, u, docs, _ = env
    from app.router import dispatch
    from app.security import make_token
    st, r = dispatch("POST", "/api/events/test", {}, {}, make_token("sis@x"))
    assert st == 200 and r["ok"] is True
    _esperar(n8n, 1)
    assert n8n[0]["body"]["event"] == "prueba"
    assert dispatch("POST", "/api/events/test", {}, {}, make_token("ab@x"))[0] == 403    # solo Sistemas
    monkeypatch.delenv("N8N_WEBHOOK_URL")
    st, r = dispatch("POST", "/api/events/test", {}, {}, make_token("sis@x"))
    assert r["ok"] is False and r["configured"] is False
    st, h = dispatch("GET", "/api/health", {}, {}, make_token("sis@x"))
    assert h["n8n"] == {"configured": False, "secret": True}


def test_usuario_de_servicio_pide_pero_no_decide(env):
    services, u, docs, _ = env
    from app.router import dispatch
    from app.security import make_token
    con = services.connect()
    con.execute("INSERT INTO users(email,name,role,pw_hash) VALUES('n8n@x','Automatización (n8n)',?,'x')", (SERVICE_ROLE,))
    con.commit(); con.close()
    bot = services.get_user("n8n@x")
    assert bot["role"] == SERVICE_ROLE
    o = pedir(services, {"ing@x": bot})                      # puede crear pedidos
    assert o["status"] == "pendiente"
    with pytest.raises(PermissionDenied):
        services.approve(bot, o["id"])
    with pytest.raises(PermissionDenied):
        services.revert(bot, o["id"])
    with pytest.raises(PermissionDenied):
        services.list_users(bot)
    tok = make_token("n8n@x", hours=24 * 365)
    assert dispatch("GET", "/api/orders", {}, {}, tok)[0] == 200
    assert dispatch("POST", f"/api/orders/{o['id']}/approve", {}, {"folders": []}, tok)[0] == 403
    con = services.connect()
    con.execute("UPDATE users SET active=0 WHERE email='n8n@x'"); con.commit(); con.close()
    assert dispatch("GET", "/api/orders", {}, {}, tok)[0] == 401      # desactivado: el token deja de servir


def test_vista_previa_de_limpieza_para_n8n(env):
    services, u, docs, _ = env
    from app.router import dispatch
    from app.security import make_token
    d = docs / "MODULOS/MDA300"
    (d / "MDA300_L1V1R0_10-10-24.xlsx").write_bytes((d / "MDA300_L1V1R2_10-10-24.xlsx").read_bytes())
    (d / "MDA300_L1V1R1_10-10-24.xlsx").write_bytes(b"r1")
    con = services.connect()
    con.execute("INSERT INTO users(email,name,role,pw_hash) VALUES('n8n@x','Automatización (n8n)',?,'x')", (SERVICE_ROLE,))
    con.commit(); con.close()
    tok = make_token("n8n@x")
    st, r = dispatch("GET", "/api/cleanup/preview", {}, {}, tok)
    assert st == 200 and r["count"] == 2 and not r["truncated"]
    assert (d / "MDA300_L1V1R0_10-10-24.xlsx").exists()               # solo mira, no mueve nada
    assert dispatch("POST", "/api/cleanup", {}, {"dry_run": False}, tok)[0] == 403   # ejecutar la limpieza no le toca
    assert dispatch("GET", "/api/cleanup/preview", {}, {}, make_token("ab@x"))[0] == 403
