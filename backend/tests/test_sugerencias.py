"""Columna 'Sugerencias' en las planillas y pedidos de reemplazos que se escriben en ellas."""
import sys
from pathlib import Path
import pytest
from openpyxl import Workbook, load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import engine, ods  # noqa: E402
from test_formatos import make_ods  # noqa: E402


def _xlsx(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook(); ws = wb.active; ws.title = "Items"
    ws.append(["Codigo", "Descripcion", "Cantidad"])
    ws.append(["DIS206AA", "Disipador", 4]); ws.append(["X-1", "otro", 1]); ws.append(["DIS206AA", "dup", 2])
    wb.save(p)


def test_xlsx_columna_nueva_y_sin_repetir(tmp_path):
    p = tmp_path / "A_L1V1R0.xlsx"; _xlsx(p)
    d = engine.open_doc(p)
    ch = d.add_suggestions("dis206aa", ["DIS206BA", "DIS206CA"])
    assert len(ch) == 2
    d.save(tmp_path / "o.xlsx")
    ws = load_workbook(tmp_path / "o.xlsx")["Items"]
    assert ws["D1"].value == "Sugerencias" and ws["D2"].value == "DIS206BA, DIS206CA" and ws["D4"].value == "DIS206BA, DIS206CA"
    assert ws["D3"].value is None
    d2 = engine.open_doc(tmp_path / "o.xlsx")                       # segunda vez: suma solo lo nuevo
    assert len(d2.add_suggestions("DIS206AA", ["DIS206CA", "DIS206DA"])) == 2
    d2.save(tmp_path / "o2.xlsx")
    assert load_workbook(tmp_path / "o2.xlsx")["Items"]["D2"].value == "DIS206BA, DIS206CA, DIS206DA"
    assert engine.open_doc(tmp_path / "o2.xlsx").add_suggestions("DIS206AA", ["DIS206CA"]) == []


def test_ods_columna_nueva(tmp_path):
    p = tmp_path / "A_L1V1R0.ods"; make_ods(p)
    d = engine.open_doc(p)
    ch = d.add_suggestions("DIS206AA", ["DIS206BA"])
    assert len(ch) == 2
    d.save(tmp_path / "o.ods")
    cells = {(s, c): t for s, c, t in ods.OdsDoc(tmp_path / "o.ods").cells()}
    assert cells[("Items", "D1")] == "Sugerencias"
    assert cells[("Items", "D2")] == "DIS206BA" and cells[("Items", "D6")] == "DIS206BA"
    assert ("Items", "D5") not in cells                              # la fila de OTRO-1 no recibe nada
    assert cells[("Items", "C2")] == "4" and cells[("Items", "B6")] == "dup"      # lo demas sigue igual
    d2 = engine.open_doc(tmp_path / "o.ods")
    assert d2.add_suggestions("DIS206AA", ["DIS206BA"]) == []


# ---- pedidos
@pytest.fixture()
def env(tmp_path, monkeypatch):
    docs, data = tmp_path / "docs", tmp_path / "data"
    monkeypatch.setenv("DOCS_ROOT", str(docs)); monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.delenv("LINK_MAP", raising=False); monkeypatch.delenv("EXTRA_ROOTS", raising=False)
    from app import db, services
    from app.security import hash_password
    db.init_db(); con = db.connect()
    for e, n, r in [("ing@x", "Luca", "Ingeniería"), ("ger@x", "Priscila", "Gerencia"), ("sis@x", "Nico", "Sistemas")]:
        con.execute("INSERT INTO users(email,name,role,pw_hash) VALUES(?,?,?,?)", (e, n, r, hash_password("clave1234")))
    con.commit(); con.close()
    _xlsx(docs / "MODULOS/MDA100/MDA100_L1V1R0_14-07-22.xlsx")
    _xlsx(docs / "MODULOS/MDA200/MDA200_L1V1R0_01-02-25.xlsx")
    make_ods(docs / "MODULOS/MDA300/MDA300_L1V1R0_10-10-24.ods")
    return services, {k: services.get_user(k) for k in ("ing@x", "ger@x", "sis@x")}, docs


def _tree(docs):
    return {p.relative_to(docs).as_posix(): p.read_bytes() for p in docs.rglob("*") if p.is_file()}


def test_reemplazos_en_planillas_con_reversion(env):
    services, u, docs = env
    antes = _tree(docs)
    o = services.create_order(u["ing@x"], "reemplazos", {"code": "DIS206AA", "alts": "DIS206BA, DIS206CA", "planillas": True, "reason": "sugeridos"})
    assert o["summary"]["files"] == 3
    o = services.approve(u["ger@x"], o["id"], "ok", folders=["MODULOS/MDA100", "MODULOS/MDA300"])      # sacar una carpeta
    assert o["status"] == "completado", o["result"]
    assert len(o["result"]["done"]) == 2
    assert (docs / "MODULOS/MDA100/OBSOLETO/MDA100_L1V1R0_14-07-22.xlsx").exists()
    nuevo = next(p for p in (docs / "MODULOS/MDA100").iterdir() if p.suffix == ".xlsx")
    ws = load_workbook(nuevo)
    assert ws["Items"]["D2"].value == "DIS206BA, DIS206CA" and "Control de cambios" in ws.sheetnames
    assert services.code_info(u["ing@x"], "DIS206AA")["alts"] == ["DIS206BA", "DIS206CA"]
    assert (docs / "MODULOS/MDA200/MDA200_L1V1R0_01-02-25.xlsx").read_bytes() == antes["MODULOS/MDA200/MDA200_L1V1R0_01-02-25.xlsx"]
    services.revert(u["ger@x"], o["id"])
    assert _tree(docs) == antes
    assert services.code_info(u["ing@x"], "DIS206AA")["alts"] == []


def test_reemplazos_solo_catalogo_sigue_igual(env):
    services, u, docs = env
    antes = _tree(docs)
    o = services.create_order(u["ing@x"], "reemplazos", {"code": "DIS206AA", "alts": "DIS206BA"})
    o = services.approve(u["ger@x"], o["id"])
    assert o["status"] == "completado" and o["result"]["added"] == ["DIS206BA"]
    assert _tree(docs) == antes


def test_reemplazos_en_planillas_codigo_que_no_esta(env):
    services, u, docs = env
    services.generate_code(u["ing@x"], "SOLOCAT1", "solo catalogo")
    with pytest.raises(ValueError, match="no aparece en ninguna planilla"):
        services.create_order(u["ing@x"], "reemplazos", {"code": "SOLOCAT1", "alts": "OTRO9", "planillas": True})


def test_cambio_de_codigo_con_columna_de_sugerencias(env):
    services, u, docs = env
    o = services.create_order(u["ing@x"], "codigo", {"from": "DIS206AA", "to": "DIS206ZZ", "rev_mode": "rev", "reason": "x",
                                                     "alts": "DIS206YY", "suggest_col": True})
    assert o["params"]["suggest_col"] is True
    o = services.approve(u["ger@x"], o["id"])
    assert o["status"] == "completado"
    nuevo = next(p for p in (docs / "MODULOS/MDA100").iterdir() if p.suffix == ".xlsx")
    ws = load_workbook(nuevo)["Items"]
    assert ws["A2"].value == "DIS206ZZ" and ws["D2"].value == "DIS206YY"
    # sin alts no hay columna aunque se pida
    o2 = services.create_order(u["ing@x"], "codigo", {"from": "DIS206ZZ", "to": "DIS206WW", "suggest_col": True})
    assert o2["params"]["suggest_col"] is False
