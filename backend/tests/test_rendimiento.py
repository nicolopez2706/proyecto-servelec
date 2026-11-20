import json
import pytest
from test_core import env  # noqa: F401
from app import engine
from app.config import settings


def test_el_indice_no_se_reescribe_si_no_cambio_nada(env):
    services, u, docs, _ = env
    engine.reset_cache()
    engine.search_full(docs, "DIS206AA")                    # primera vez: lee y guarda
    f = engine._cache_file()
    m1 = f.stat().st_mtime_ns
    engine.scan(docs)                                        # nada cambio: no debe reescribir
    engine.search_full(docs, "DIS206AA")
    assert f.stat().st_mtime_ns == m1


def test_busqueda_reusa_el_recorrido_y_los_cambios_lo_descartan(env, monkeypatch):
    services, u, docs, _ = env
    monkeypatch.setenv("SCAN_TTL", "300")
    engine.reset_cache()
    r1 = engine.search_full(docs, "DIS206AA")["items"]
    assert r1
    llamadas = []
    real = engine.discover
    monkeypatch.setattr(engine, "discover", lambda root: (llamadas.append(1), real(root))[1])
    assert engine.search_full(docs, "DIS206AA")["items"] == r1
    assert not llamadas                                      # segunda busqueda: sin recorrer de nuevo
    engine.simulate(docs, "DIS206AA")                        # una simulacion siempre recorre
    assert llamadas
    llamadas.clear()
    engine.invalidate_scan()                                 # lo que hace cualquier cambio de archivos
    engine.search_full(docs, "DIS206AA")
    assert llamadas
    monkeypatch.setenv("SCAN_TTL", "0")                      # 0 = siempre recorrer
    llamadas.clear()
    engine.search_full(docs, "DIS206AA"); engine.search_full(docs, "DIS206AA")
    assert len(llamadas) == 2


def test_un_pedido_aprobado_se_ve_en_la_busqueda_enseguida(env, monkeypatch):
    services, u, docs, _ = env
    monkeypatch.setenv("SCAN_TTL", "300")
    engine.reset_cache()
    assert engine.search_full(docs, "DIS206BA")["items"] == []
    o = services.create_order(u["ing@x"], "codigo", {"from": "DIS206AA", "to": "DIS206BA", "rev_mode": "rev"})
    services.approve(u["ger@x"], o["id"])
    assert engine.search_full(docs, "DIS206BA")["items"]    # aparece sin esperar a que venza el tiempo


def test_el_filtro_rapido_no_pierde_resultados(env):
    services, u, docs, _ = env
    engine.reset_cache()
    a = engine.search_full(docs, "dis206aa")["items"]        # minusculas
    b = engine.search_full(docs, "IS206A")["items"]          # parte del codigo
    assert a and b and {x["file"] for x in a} <= {x["file"] for x in b}
    assert engine.search_full(docs, "NOEXISTE999")["items"] == []
    assert engine.simulate(docs, "DIS206AA")["total_files"] >= 1


def test_un_pedido_lento_no_congela_el_resto_del_portal(env, monkeypatch):
    """Antes, un pedido de la API que tardaba (recorrer carpetas, LibreOffice) bloqueaba TODO el servidor,
    incluso la pantalla principal: el navegador se quedaba cargando."""
    pytest.importorskip("fastapi")
    import asyncio, time
    from app import main

    def lento(*a, **k):
        time.sleep(1.5)
        return 200, {"ok": True}
    monkeypatch.setattr(main, "dispatch", lento)

    async def pedir(path):
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
                 "path": path, "raw_path": path.encode(), "query_string": b"", "headers": [], "server": ("t", 80),
                 "client": ("c", 1), "root_path": ""}
        estado = {}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(m):
            if m["type"] == "http.response.start":
                estado["status"] = m["status"]
        await main.app(scope, receive, send)
        return estado.get("status")

    async def correr():
        t0 = time.monotonic()
        tarea = asyncio.create_task(pedir("/api/lento"))
        await asyncio.sleep(0.2)
        code = await pedir("/")                               # la pantalla principal
        dt = time.monotonic() - t0
        await tarea
        return code, dt
    code, dt = asyncio.run(correr())
    assert code == 200
    assert dt < 1.0


def test_los_avisos_del_sistema_no_hacen_esperar_al_entrar(env, monkeypatch):
    """/api/me no debe quedar esperando el recorrido de todas las carpetas (la pantalla quedaba en blanco)."""
    import time, threading
    services, u, docs, _ = env
    engine._WARN.clear()
    soltar = threading.Event()
    real = engine.discover

    def lento(root):
        soltar.wait(5)
        return real(root)
    monkeypatch.setattr(engine, "discover", lento)
    t0 = time.monotonic()
    assert engine.system_warnings(docs, wait=False) == []      # no espera
    assert time.monotonic() - t0 < 1.0
    soltar.set()
    for _ in range(50):                                        # el calculo termina en segundo plano
        if str(docs) in engine._WARN:
            break
        time.sleep(0.1)
    assert str(docs) in engine._WARN
    assert engine.system_warnings(docs, wait=False) == engine._WARN[str(docs)][1]


def test_pedido_interrumpido_se_recupera_al_arrancar(env):
    from test_core import pedir
    services, u, docs, data = env
    a = pedir(services, u)
    b = pedir(services, u, to="DIS206CA")
    con = services.connect()
    con.execute("UPDATE orders SET status='ejecutando' WHERE id IN (?,?)", (a["id"], b["id"]))
    con.commit(); con.close()
    bk = settings.backup_dir / f"pedido_{b['id']}" / "A"        # el segundo ya tenia un respaldo hecho
    bk.mkdir(parents=True); (bk / "x.xlsx").write_text("x")
    assert sorted(services.recover_interrupted()) == sorted([a["id"], b["id"]])
    con = services.connect()
    est = {r["id"]: r["status"] for r in con.execute("SELECT id,status FROM orders")}
    con.close()
    assert est[a["id"]] == "pendiente"                          # sin respaldos: se puede aprobar de nuevo
    assert est[b["id"]] == "falló"                              # con respaldos: hay que revisar a mano
    assert services.recover_interrupted() == []


def test_error_inesperado_no_deja_el_pedido_ejecutando(env, monkeypatch):
    from test_core import pedir
    services, u, docs, data = env
    o = pedir(services, u)
    monkeypatch.setattr(services, "_execute", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        services.approve(u["ger@x"], o["id"])
    assert services.get_order(u["ger@x"], o["id"])["status"] == "falló"


def test_servidor_de_red_que_no_responde_no_cuelga_la_lectura_de_accesos_directos(monkeypatch):
    """Los .lnk guardan tambien la ruta \\\\servidor\\recurso. Si ese servidor no se alcanza, no hay que preguntarle a Windows
    si el archivo existe (tarda decenas de segundos por cada acceso directo)."""
    import socket
    engine._HOSTS.clear()
    intentos = []

    def sin_red(addr, timeout=None):
        intentos.append(addr)
        raise OSError("sin ruta al servidor")
    monkeypatch.setattr(socket, "create_connection", sin_red)

    def no_debe_llamarse(p):
        raise AssertionError(f"se consulto el sistema de archivos en {p}")
    monkeypatch.setattr(engine.os.path, "exists", no_debe_llamarse)
    monkeypatch.setattr(engine.os.path, "isdir", no_debe_llamarse)
    assert engine._exists(r"\\192.168.1.2\Servelec\Doc_Productos\A\x.xlsx") is False
    assert engine._isdir(r"\\192.168.1.2\Servelec\Doc_Productos\A") is False
    assert engine._exists("//192.168.1.2/Servelec/x.xlsx") is False
    assert len(intentos) == 1                                   # se prueba una vez y se recuerda
    engine._HOSTS.clear()


def test_rutas_locales_y_unidades_no_usan_la_prueba_de_red(tmp_path, monkeypatch):
    import socket
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debia probar la red")))
    f = tmp_path / "x.xlsx"
    f.write_text("x")
    assert engine._exists(str(f)) and engine._isdir(str(tmp_path))
    assert engine._reachable("Z:\\Doc_Productos\\x") is True


def test_consulta_a_la_red_que_se_cuelga_se_corta_por_tiempo(monkeypatch):
    """Una unidad de red recordada pero desconectada deja a Windows esperando minutos: hay que cortar por tiempo."""
    import socket, time, threading
    engine._HOSTS.clear(); engine._DRV.clear()
    monkeypatch.setattr(engine, "NET_TIMEOUT", 0.3)
    monkeypatch.setattr(engine, "_drive_host", lambda letter: "servidor" if letter == "Z" else None)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: __import__("contextlib").nullcontext())
    liberar = threading.Event()
    llamadas = []

    def colgado(p):
        llamadas.append(p)
        liberar.wait(5)
        return True
    t0 = time.monotonic()
    assert engine._net_call(colgado, "Z:\\Doc_Productos\\x.xlsx") is False
    assert time.monotonic() - t0 < 2
    t1 = time.monotonic()
    assert engine._net_call(colgado, "Z:\\Doc_Productos\\y.xlsx") is False      # ya marcada como caida: no vuelve a esperar
    assert time.monotonic() - t1 < 0.2 and len(llamadas) == 1
    assert engine._net_call(lambda p: True, "C:\\local\\x.xlsx") is True      # lo local no se toca
    liberar.set()
    engine._HOSTS.clear(); engine._DRV.clear()
