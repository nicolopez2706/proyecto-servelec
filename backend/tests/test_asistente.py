"""El asistente entiende frases en lenguaje natural, arma el cambio y solo crea el pedido cuando la persona confirma.
Nunca aprueba, rechaza, revierte ni limpia."""
import inspect

import pytest
from test_core import env  # noqa: F401
from app import assistant, db, router
from app.security import make_token


def preguntar(services, user, texto):
    return assistant.handle(user, {"message": texto})


def test_cambio_arma_la_simulacion_y_ofrece_confirmar(env):
    services, u, *_ = env
    r = preguntar(services, u["ing@x"], "cambiar DIS206AA por DIS206BA")
    texto = " ".join(r["bullets"])
    assert "4 celdas" in texto and "3 archivos" in texto and "3 carpetas" in texto and "Nada se modifica hasta que lo apruebe otra persona" in texto
    assert r["actions"][0]["do"] == "create_order" and r["actions"][0]["params"]["to"] == "DIS206BA"
    # todavia no se creo nada: el asistente solo propone
    assert services.list_orders(u["ing@x"]) == []
    # la persona confirma con el boton -> recien ahi se crea, con los permisos de esa persona
    ok = assistant.handle(u["ing@x"], {"do": "create_order", "params": r["actions"][0]["params"]})
    ordenes = services.list_orders(u["ing@x"])
    assert len(ordenes) == 1 and ordenes[0]["status"] == "pendiente" and ordenes[0]["created_by"] == "ing@x"
    assert "pedido #1" in ok["text"] and "no puedo aprobarlo" in " ".join(ok["bullets"])


def test_cambio_con_cantidades_motivo_y_version_nueva(env):
    services, u, *_ = env
    r = preguntar(services, u["ing@x"], "Cambiá el código dis206aa por el dis206ba con cantidades x2 en versión nueva porque lo pidió Luca")
    p = r["actions"][0]["params"]
    assert p["qty"]["on"] and p["qty"]["factor"] == 2.0 and p["rev_mode"] == "ver" and p["reason"] == "lo pidió Luca"
    assert "V+1" in " ".join(r["bullets"]) and "por cada 1 de DIS206AA van 2 de DIS206BA" in " ".join(r["bullets"])
    assistant.handle(u["ing@x"], {"do": "create_order", "params": p})
    o = services.list_orders(u["ing@x"])[0]
    assert o["params"]["qty"]["factor"] == 2.0 and o["params"]["rev_mode"] == "ver"


def test_cantidades_sin_factor_avisa_en_vez_de_inventar(env):
    services, u, *_ = env
    r = preguntar(services, u["ing@x"], "cambiar DIS206AA por DIS206BA con cantidades")
    assert "no entendí el factor" in " ".join(r["bullets"]) and not r["actions"][0]["params"]["qty"]["on"]


def test_codigo_inexistente_o_igual_no_ofrece_confirmar(env):
    services, u, *_ = env
    r = preguntar(services, u["ing@x"], "cambiar NOEXISTE por DIS206BA")
    assert r["error"] and "No se encontró NOEXISTE" in r["text"] and "actions" not in r
    r = preguntar(services, u["ing@x"], "cambiar DIS206AA por DIS206AA")
    assert r["error"] and "igual" in r["text"]


def test_nunca_aprueba_revierte_ni_limpia(env):
    services, u, *_ = env
    o = services.create_order(u["ing@x"], "codigo", {"from": "DIS206AA", "to": "DIS206BA"})
    for frase in ("aprobá el pedido 1", "aprobar pedido 1", "revertí el pedido 1", "ejecutá la limpieza", "rechazá el pedido 1", "cancelá el pedido 1"):
        r = preguntar(services, u["ger@x"], frase)
        assert "Eso no lo hago yo" in r["text"], frase
    assert services.get_order(u["ger@x"], o["id"])["status"] == "pendiente"
    # ni siquiera si se lo piden "por la puerta de atras" con una accion armada a mano
    for accion in ("approve", "revert", "run_cleanup", "reject", "cancel", "set_role", "create_user"):
        with pytest.raises(ValueError):
            assistant.handle(u["ger@x"], {"do": accion, "params": {"id": o["id"]}})
    assert services.get_order(u["ger@x"], o["id"])["status"] == "pendiente"
    # y el codigo del asistente no llama a ninguna funcion que apruebe o revierta
    fuente = inspect.getsource(assistant)
    for prohibido in ("services.approve", "services.revert", "services.run_cleanup", "services.reject", "services.cancel", "services.set_role", "services.create_user", "services.reset_password"):
        assert prohibido not in fuente


def test_pedir_aprobacion_en_el_mismo_mensaje_lo_aclara(env):
    services, u, *_ = env
    r = preguntar(services, u["ing@x"], "cambiar DIS206AA por DIS206BA y aprobalo")
    assert "Yo no apruebo pedidos" in " ".join(r["bullets"]) and services.list_orders(u["ing@x"]) == []


def test_info_de_un_codigo_y_busqueda_por_descripcion(env):
    services, u, *_ = env
    r = preguntar(services, u["ab@x"], "¿Dónde está DIS206AA?")
    assert r["codes"][0]["code"] == "DIS206AA" and "4 celdas" in " ".join(r["bullets"])
    assert r["links"][0]["href"].endswith("from=DIS206AA")
    r = preguntar(services, u["ab@x"], "busco un disipador")
    assert [c["code"] for c in r["codes"]] == ["DIS206AA"]
    r = preguntar(services, u["ab@x"], "DIS206")                           # parte del codigo
    assert r["codes"][0]["code"] == "DIS206AA"
    r = preguntar(services, u["ab@x"], "ver el disipador")                 # "ver <palabra>" cae a buscar por descripcion
    assert r["codes"][0]["code"] == "DIS206AA"


def test_sin_resultados_abastecimiento_puede_avisar_ingenieria_no(env):
    services, u, *_ = env
    r = preguntar(services, u["ab@x"], "busco tornillo M8 inoxidable")
    assert r["actions"][0]["do"] == "request_code"
    r2 = preguntar(services, u["ing@x"], "busco tornillo M8 inoxidable")
    assert "actions" not in r2


def test_aviso_de_codigo_que_falta(env):
    services, u, *_ = env
    r = preguntar(services, u["ab@x"], "avisar que falta el tornillo M8 inox")
    assert "tornillo M8 inox" in r["text"] and r["actions"][0]["do"] == "request_code"
    assert services.list_requests(u["ing@x"]) == []                        # todavia no se envio
    ok = assistant.handle(u["ab@x"], {"do": "request_code", "params": r["actions"][0]["params"]})
    assert "le avisé a Ingeniería" in ok["text"]
    pedidos = services.list_requests(u["ing@x"])
    assert len(pedidos) == 1 and "tornillo M8 inox" in pedidos[0]["what"] and pedidos[0]["by"] == "ab@x"
    # Ingenieria no avisa: genera el codigo
    r = preguntar(services, u["ing@x"], "falta el tornillo M8 inox")
    assert "Abastecimiento" in r["text"] and "actions" not in r


def test_estado_de_los_pedidos(env):
    services, u, *_ = env
    assert "Todavía no hiciste pedidos" in preguntar(services, u["ing@x"], "mis pedidos")["text"]
    o = services.create_order(u["ing@x"], "codigo", {"from": "DIS206AA", "to": "DIS206BA"})
    r = preguntar(services, u["ing@x"], "mis pedidos")
    assert f"#{o['id']} · Cambiar DIS206AA por DIS206BA · espera aprobación" in r["bullets"][0]
    r = preguntar(services, u["ger@x"], "pedido 1")
    assert "espera aprobación" in r["text"] and "quien lo pidió no puede aprobarlo" in " ".join(r["bullets"])
    assert preguntar(services, u["ger@x"], "pedido 99")["error"]
    # quien aprueba ve lo pendiente de los demas; quien pidio, lo suyo
    assert preguntar(services, u["ger@x"], "pedidos pendientes")["text"].startswith("Pedidos que esperan tu aprobación")
    # Ingenieria puede aprobar pero no su propio pedido; con "mis pedidos pendientes" ve los suyos
    assert preguntar(services, u["ing@x"], "pedidos pendientes")["text"] == "No hay pedidos esperando tu aprobación."
    assert preguntar(services, u["ing@x"], "mis pedidos pendientes")["text"].startswith("Tus pedidos pendientes")
    # Abastecimiento no aprueba: ve los suyos
    services.create_order(u["ab@x"], "codigo", {"from": "OTRO", "to": "OTRO2"})
    assert preguntar(services, u["ab@x"], "pedidos pendientes")["text"].startswith("Tus pedidos pendientes (1 de 1)")


def test_ayuda_segun_el_area(env):
    services, u, *_ = env
    assert any("avisar a Ingeniería" in b for b in preguntar(services, u["ab@x"], "hola")["bullets"])
    assert not any("avisar a Ingeniería" in b for b in preguntar(services, u["ing@x"], "ayuda")["bullets"])
    assert preguntar(services, u["ing@x"], "?")["text"].startswith("Hola")


def test_api_http(env):
    services, u, *_ = env
    tok = make_token("ing@x")
    st, d = router.dispatch("POST", "/api/assistant", {}, {"message": "dónde está DIS206AA"}, tok)
    assert st == 200 and d["codes"][0]["code"] == "DIS206AA"
    assert router.dispatch("POST", "/api/assistant", {}, {"message": "hola"}, None)[0] == 401
    assert router.dispatch("POST", "/api/assistant", {}, {"message": "   "}, tok)[0] == 400
    assert router.dispatch("POST", "/api/assistant", {}, {"do": "approve"}, tok)[0] == 400
    # el usuario de servicio (n8n) tambien puede preguntar, con sus permisos
    from app.permissions import SERVICE_ROLE
    from app.security import hash_password
    con = db.connect()
    con.execute("INSERT INTO users(email,name,role,pw_hash) VALUES('n8n@x','n8n',?,?)", (SERVICE_ROLE, hash_password("x" * 20)))
    con.commit(); con.close()
    assert router.dispatch("POST", "/api/assistant", {}, {"message": "hola"}, make_token("n8n@x"))[0] == 200


def test_texto_largo_se_recorta_y_html_no_se_interpreta(env):
    services, u, *_ = env
    r = preguntar(services, u["ing@x"], "busco <script>alert(1)</script> " + "x" * 5000)
    assert "texto" not in r and len(r["text"]) < 300             # el texto del usuario no vuelve en la respuesta sin escapar ni se desborda
