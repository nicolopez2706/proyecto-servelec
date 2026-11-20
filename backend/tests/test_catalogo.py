import pytest
from test_core import env, pedir  # noqa: F401  (reutiliza la carpeta de prueba)
from app.permissions import PermissionDenied


def test_catalogo_lista_codigos_de_planillas_con_descripcion(env):
    services, u, *_ = env
    rows = {r["code"]: r for r in services.catalog_list(u["ab@x"])}
    assert rows["DIS206AA"]["cells"] == 4 and rows["DIS206AA"]["folders"] == 3 and rows["DIS206AA"]["desc"] == "Disipador"
    assert [r["code"] for r in services.catalog_list(u["ab@x"], "disip")] == ["DIS206AA"]   # busca por descripcion
    info = services.code_info(u["ab@x"], "dis206aa")
    assert len(info["where"]) == 3


def test_solicitud_y_generacion_de_codigo(env):
    services, u, *_ = env
    with pytest.raises(PermissionDenied): services.request_code(u["ing@x"], "tornillo M8 inox")        # Ingenieria no pide
    rid = services.request_code(u["ab@x"], "No encuentro la arandela de 8 mm de acero")["id"]
    assert [r["id"] for r in services.list_requests(u["ab@x"])] == [rid]
    assert services.list_requests(u["ing@x"])[0]["status"] == "abierta"
    with pytest.raises(PermissionDenied): services.generate_code(u["ab@x"], "ARA8", "x")
    g = services.generate_code(u["ing@x"], "ara8-inox", "Arandela 8 mm acero inoxidable", "insumo", "ARA8-ZIN, ara8-zin ,ARA8-INOX", rid)
    assert g["alts"] == ["ARA8-ZIN"]                                  # sin repetidos ni el mismo codigo
    assert services.list_requests(u["ab@x"])[0]["status"] == "resuelta" and services.list_requests(u["ab@x"])[0]["code"] == "ARA8-INOX"
    with pytest.raises(ValueError): services.generate_code(u["ing@x"], "ARA8-INOX", "otra")
    assert any(r["code"] == "ARA8-INOX" and r["in_catalog"] for r in services.catalog_list(u["ger@x"]))


def test_rechazar_solicitud(env):
    services, u, *_ = env
    rid = services.request_code(u["ab@x"], "algo que no hace falta")["id"]
    services.reject_request(u["ing@x"], rid, "ya existe")
    assert services.list_requests(u["ab@x"])[0]["status"] == "rechazada"
    with pytest.raises(ValueError): services.reject_request(u["ing@x"], rid)


def test_pedido_de_reemplazos_se_aprueba_y_se_revierte(env):
    services, u, docs, _ = env
    antes = {p: p.read_bytes() for p in docs.rglob("*") if p.is_file()}
    o = services.create_order(u["ab@x"], "reemplazos", {"code": "DIS206AA", "alts": "DIS207AA, DIS208AA"})
    assert o["type"] == "reemplazos" and o["status"] == "pendiente"
    with pytest.raises(PermissionDenied): services.approve(u["ab@x"], o["id"])
    r = services.approve(u["ing@x"], o["id"])
    assert r["status"] == "completado" and r["result"]["added"] == ["DIS207AA", "DIS208AA"]
    assert services.code_info(u["ab@x"], "DIS206AA")["alts"] == ["DIS207AA", "DIS208AA"]
    assert {p: p.read_bytes() for p in docs.rglob("*") if p.is_file()} == antes          # no toca planillas
    services.revert(u["ger@x"], o["id"])
    assert services.code_info(u["ab@x"], "DIS206AA")["alts"] == []
    with pytest.raises(ValueError): services.create_order(u["ab@x"], "reemplazos", {"code": "NOEXISTE9", "alts": "A1"})
    with pytest.raises(ValueError): services.create_order(u["ab@x"], "reemplazos", {"code": "DIS206AA", "alts": ""})


def test_cambio_con_alts_y_conversion_guardada(env):
    services, u, *_ = env
    pv = services.preview(u["ing@x"], {"from": "DIS206AA", "to": "DIS206BA", "qty": {"on": True, "factor": 2}, "alts": "DIS206CA"})
    assert pv["sim"]["total_cells"] == 4 and pv["conv"] is None and pv["params"]["alts"] == ["DIS206CA"]
    assert services.list_orders(u["ing@x"]) == []                      # la vista previa no guarda nada
    o = services.create_order(u["ing@x"], "codigo", {"from": "DIS206AA", "to": "DIS206BA", "qty": {"on": True, "factor": 2, "note": "va doble"}, "alts": "DIS206CA"})
    r = services.approve(u["ger@x"], o["id"])
    assert services.code_info(u["ab@x"], "DIS206BA")["alts"] == ["DIS206CA"]
    assert services.get_conv(u["ab@x"], "DIS206AA", "DIS206BA") == {"factor": 2.0, "note": "va doble"}
    pv2 = services.preview(u["ing@x"], {"from": "DIS206BA", "to": "DIS206AA"})
    assert pv2["conv"] is None
    services.revert(u["ger@x"], o["id"])                                 # revertir saca tambien reemplazos y conversion
    assert services.get_conv(u["ab@x"], "DIS206AA", "DIS206BA") is None
    assert services.code_info(u["ab@x"], "DIS206AA")["cells"] == 4


def test_preview_avisos(env):
    services, u, docs, _ = env
    pv = services.preview(u["ing@x"], {"from": "DIS206AA", "to": "OTRO"})
    assert any("OTRO ya se usa en 2 celdas" in w for w in pv["warnings"])    # ya existe (sin distinguir mayusculas)
    assert any("omitir" in w for w in pv["warnings"])                  # el .ods roto
    with pytest.raises(ValueError): services.preview(u["ing@x"], {"from": "NOEXISTE", "to": "X1"})


def test_estado_revisiones_y_busqueda(env):
    services, u, *_ = env
    st = services.revision_status(u["ing@x"])
    assert st["vigentes"] == 4 and st["por_limpiar"] == []
    assert services.search(u["ab@x"], "disip")["items"]
    with pytest.raises(ValueError): services.search(u["ab@x"], "di")
    with pytest.raises(PermissionDenied): services.revision_status(u["ab@x"])


def test_no_encontrado_explica_los_archivos_que_no_se_leyeron(tmp_path, monkeypatch):
    """Si un archivo no se puede leer, el aviso debe decirlo en vez de afirmar que el codigo no existe."""
    from app import engine
    monkeypatch.setenv("DOCS_ROOT", str(tmp_path / "docs")); monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(engine, "find_soffice", lambda: None)
    docs = tmp_path / "docs"; (docs / "A").mkdir(parents=True)
    (docs / "A" / "LDM-X_D1V1R0.xls").write_bytes(b"\xd0\xcf\x11\xe0 T46081")
    engine.reset_cache()
    sim = engine.simulate(docs, "T46081")
    assert sim["total_cells"] == 0 and len(sim["skipped"]) == 1
    txt = engine.explain_skipped(sim["skipped"])
    assert "NO se pudieron leer" in txt and "LibreOffice" in txt
    res = engine.search_full(docs, "T46081")
    assert res["items"] == [] and "LibreOffice" in res["skipped_note"]
    w = engine.system_warnings(docs, ttl=0)
    assert w and w[0]["code"] == "sin_libreoffice" and w[0]["count"] == 1
