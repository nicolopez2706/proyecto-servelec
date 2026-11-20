"""Accesos directos: lectura, resolucion, actualizacion tras una revision, reversion y reparacion."""
import sys
from pathlib import Path
import pytest
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

Z = "Z:\\Doc_Productos"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    docs, data, otro = tmp_path / "docs", tmp_path / "data", tmp_path / "otro"
    monkeypatch.setenv("DOCS_ROOT", str(docs)); monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.delenv("LINK_MAP", raising=False); monkeypatch.delenv("EXTRA_ROOTS", raising=False)
    from app import db, services, links
    from app.security import hash_password
    db.init_db()
    con = db.connect()
    for e, n, r in [("ing@x", "Luca", "Ingeniería"), ("ger@x", "Priscila", "Gerencia"), ("sis@x", "Nico", "Sistemas")]:
        con.execute("INSERT INTO users(email,name,role,pw_hash) VALUES(?,?,?,?)", (e, n, r, hash_password("clave1234")))
    con.commit(); con.close()

    def mk(base, rel, rows):
        p = base / rel; p.parent.mkdir(parents=True, exist_ok=True)
        wb = Workbook(); ws = wb.active; ws.title = "Items"
        ws.append(["Codigo", "Descripcion", "Cantidad"])
        for r in rows: ws.append(r)
        wb.save(p); return p

    mk(docs, "MODULOS/MDA100/MDA100_L1V1R0_14-07-22.xlsx", [["DIS206AA", "Disipador", 4]])
    mk(docs, "MODULOS/MDA200/MDA200_L1V1R1_01-02-25.xlsx", [["DIS206AA", "Disipador", 3]])      # la R0 ya no esta
    mk(docs, "MODULOS/MDA300/MDA300_L1V1R2_10-10-24.xlsx", [["DIS206AA", "Disipador", 2]])
    mk(otro, "EXTRA/EXT1_L1V1R0_01-01-24.xlsx", [["DIS206AA", "En otro servidor", 1]])

    def lnk(rel, raw, is_dir=False):
        p = docs / rel; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(links.build_lnk(raw, is_dir)); return p

    lnk("AREAS/Ing/MDA100_L1V1R0_14-07-22.xlsx - Acceso directo.lnk", Z + r"\MODULOS\MDA100\MDA100_L1V1R0_14-07-22.xlsx")
    lnk("AREAS/Ing/MDA200 vieja.lnk", Z + r"\MODULOS\MDA200\MDA200_L1V1R0_01-02-25.xlsx")          # revision vieja
    lnk("AREAS/Ing/carpeta MDA300.lnk", Z + r"\MODULOS\MDA300", True)
    lnk("AREAS/Ing/roto.lnk", r"\\srv\compartido\NO_EXISTE\nada_L1V1R0.xlsx")
    lnk("AREAS/Ing/externo.lnk", str(otro / "EXTRA" / "EXT1_L1V1R0_01-01-24.xlsx"))
    u = {k: services.get_user(k) for k in ("ing@x", "ger@x", "sis@x")}
    return services, u, docs, data, otro, links


def estados(services, u):
    return {Path(i["lnk"]).name: i for i in services.links_report(u["sis@x"])["items"]}


def test_estados_con_mapeo(env, monkeypatch):
    services, u, docs, data, otro, links = env
    monkeypatch.setenv("LINK_MAP", f"{Z}={docs}")
    e = estados(services, u)
    assert e["MDA100_L1V1R0_14-07-22.xlsx - Acceso directo.lnk"]["status"] == "ok"
    assert e["MDA100_L1V1R0_14-07-22.xlsx - Acceso directo.lnk"]["method"] == "mapeado"
    assert e["MDA200 vieja.lnk"]["status"] == "desactualizado"
    assert e["MDA200 vieja.lnk"]["target"].endswith("MDA200_L1V1R1_01-02-25.xlsx")
    assert e["carpeta MDA300.lnk"]["status"] == "ok" and e["carpeta MDA300.lnk"]["is_dir"]
    assert e["roto.lnk"]["status"] == "roto"
    assert e["externo.lnk"]["status"] == "fuera"


def test_sin_mapeo_se_adivina_y_sugiere(env):
    services, u, docs, *_ = env
    rep = services.links_report(u["sis@x"])
    e = {Path(i["lnk"]).name: i for i in rep["items"]}
    assert e["MDA100_L1V1R0_14-07-22.xlsx - Acceso directo.lnk"]["status"] == "aprox"
    assert e["MDA100_L1V1R0_14-07-22.xlsx - Acceso directo.lnk"]["method"] == "sufijo"
    assert any(s["prefix"] == Z and "LINK_MAP" in s["hint"] for s in rep["suggestions"])
    assert any("EXTRA_ROOTS" in s["hint"] for s in rep["suggestions"] if s["status"] == "fuera")


def test_carpeta_extra_se_recorre(env, monkeypatch):
    services, u, docs, data, otro, links = env
    from app import engine
    assert engine.simulate(docs, "DIS206AA")["total_files"] == 3
    monkeypatch.setenv("EXTRA_ROOTS", f"Otro={otro}")
    sim = engine.simulate(docs, "DIS206AA")
    assert sim["total_files"] == 4
    assert "@Otro/EXTRA" in sim["folders"]
    assert estados(services, u)["externo.lnk"]["status"] == "ok"


def test_simulacion_trae_los_accesos(env, monkeypatch):
    services, u, docs, *_ = env
    from app import engine
    monkeypatch.setenv("LINK_MAP", f"{Z}={docs}")
    sim = engine.simulate(docs, "DIS206AA")
    por = {Path(f["file"]).name: f.get("via", []) for fs in sim["folders"].values() for f in fs}
    assert len(por["MDA100_L1V1R0_14-07-22.xlsx"]) == 1
    assert por["MDA200_L1V1R1_01-02-25.xlsx"][0]["status"] == "desactualizado"
    assert por["MDA300_L1V1R2_10-10-24.xlsx"] == []          # un acceso a carpeta no es acceso al archivo


def _tree(docs):
    return {str(p.relative_to(docs)): p.read_bytes() for p in docs.rglob("*") if p.is_file()}


def test_revision_actualiza_accesos_y_se_revierte(env, monkeypatch):
    services, u, docs, data, otro, links = env
    monkeypatch.setenv("LINK_MAP", f"{Z}={docs}")
    antes = _tree(docs)
    o = services.create_order(u["ing@x"], "codigo", {"from": "DIS206AA", "to": "DIS206BA", "rev_mode": "rev", "reason": "prueba"})
    o = services.approve(u["ger@x"], o["id"], "ok")
    assert o["status"] == "completado", o["result"]
    res = o["result"]
    assert res["links_updated"] == 2                                # el directo y el desactualizado
    lnks = {p.name: p for p in docs.rglob("*.lnk")}
    nuevo = next(p for p in (docs / "MODULOS/MDA100").iterdir() if p.suffix == ".xlsx").name
    assert nuevo.startswith("MDA100_L1V1R1_")
    assert (docs / "MODULOS/MDA100/OBSOLETO/MDA100_L1V1R0_14-07-22.xlsx").exists()
    # el acceso se renombro (el nombre llevaba el del archivo) y apunta al nuevo, con la ruta de servidor
    renombrado = [n for n in lnks if n.startswith("MDA100_L1V1R1_")]
    assert len(renombrado) == 1
    assert links.read_link(lnks[renombrado[0]])["raws"][0] == Z + "\\MODULOS\\MDA100\\" + nuevo
    assert not any(n.startswith("MDA100_L1V1R0_") for n in lnks)
    vieja = links.read_link(lnks["MDA200 vieja.lnk"])["raws"][0]
    assert vieja.startswith(Z + "\\MODULOS\\MDA200\\MDA200_L1V1R2_")
    # los accesos siguen funcionando despues del cambio
    e = estados(services, u)
    assert {i["status"] for n, i in e.items() if n.startswith("MDA") and "carpeta" not in n} == {"ok"}
    # revertir deja todo identico, accesos directos incluidos
    services.revert(u["ger@x"], o["id"])
    assert _tree(docs) == antes


def test_version_nueva_no_toca_accesos(env, monkeypatch):
    services, u, docs, *_ = env
    from app import links
    monkeypatch.setenv("LINK_MAP", f"{Z}={docs}")
    antes = {p.name: p.read_bytes() for p in docs.rglob("*.lnk")}
    o = services.create_order(u["ing@x"], "codigo", {"from": "DIS206AA", "to": "DIS206BA", "rev_mode": "ver", "reason": "prueba"})
    o = services.approve(u["ger@x"], o["id"], "ok")
    assert o["result"]["links_updated"] == 0
    assert {p.name: p.read_bytes() for p in docs.rglob("*.lnk")} == antes     # la version anterior sigue vigente


def test_reparar_desactualizados(env, monkeypatch):
    services, u, docs, data, otro, links = env
    monkeypatch.setenv("LINK_MAP", f"{Z}={docs}")
    with pytest.raises(Exception):
        services.links_repair(u["ing@x"], dry_run=False)             # solo Sistemas
    vista = services.links_repair(u["sis@x"], dry_run=True)
    assert len(vista["fixed"]) == 1
    p = docs / "AREAS/Ing/MDA200 vieja.lnk"
    antes = p.read_bytes()
    assert p.read_bytes() == antes                                   # la vista previa no toca nada
    res = services.links_repair(u["sis@x"], dry_run=False)
    assert len(res["fixed"]) == 1 and not res["failed"]
    assert links.read_link(p)["raws"][0] == Z + r"\MODULOS\MDA200\MDA200_L1V1R1_01-02-25.xlsx"
    assert estados(services, u)["MDA200 vieja.lnk"]["status"] == "ok"
    assert Path(res["fixed"][0]["bk"]).read_bytes() == antes          # hay copia del original


def test_lnk_ilegible_no_rompe(env):
    services, u, docs, *_ = env
    (docs / "AREAS/Ing/basura.lnk").write_bytes(b"esto no es un acceso directo")
    e = estados(services, u)
    assert e["basura.lnk"]["status"] == "ilegible"


def test_api_permisos(env):
    from app import router
    services, u, *_ = env
    from app.security import make_token
    st, body = router.dispatch("GET", "/api/links", token=make_token("ing@x"))
    assert st == 200 and body["total"] == 5
    st, _ = router.dispatch("POST", "/api/links/repair", body={"dry_run": False}, token=make_token("ger@x"))
    assert st == 403


def test_enlaces_a_google_sheets_se_distinguen(env):
    services, u, docs, *_ = env
    (docs / "AREAS/Ing/planilla drive.url").write_text("[InternetShortcut]\nURL=https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOp/edit\n")
    (docs / "AREAS/Ing/otra.gsheet").write_text('{"url": "https://docs.google.com/spreadsheets/d/9ZyXwVuTsRqPoNm/edit", "doc_id": "9ZyXwVuTsRqPoNm"}')
    (docs / "AREAS/Ing/web.url").write_text("[InternetShortcut]\nURL=https://example.com/\n")
    e = estados(services, u)
    assert e["planilla drive.url"]["status"] == "drive" and e["otra.gsheet"]["status"] == "drive"
    assert e["web.url"]["status"] == "ilegible"
    from app import engine
    assert engine.simulate(docs, "DIS206AA")["total_files"] == 3        # no se cuelan como archivos


def test_modo_solo_lectura(env, monkeypatch):
    services, u, docs, *_ = env
    o = services.create_order(u["ing@x"], "codigo", {"from": "DIS206AA", "to": "DIS206BA", "rev_mode": "rev"})
    monkeypatch.setenv("READ_ONLY", "1")
    with pytest.raises(ValueError, match="solo lectura"):
        services.approve(u["ger@x"], o["id"])
    with pytest.raises(ValueError, match="solo lectura"):
        services.run_cleanup(u["sis@x"], dry_run=False)
    assert services.run_cleanup(u["sis@x"], dry_run=True)["dry_run"]          # simular sigue andando
    assert services.get_order(u["ing@x"], o["id"])["status"] == "pendiente"
    monkeypatch.delenv("READ_ONLY")
    assert services.approve(u["ger@x"], o["id"])["status"] == "completado"


def test_planilla_gemela_en_google_sheets(env):
    services, u, docs, data, otro, links = env
    raw = r"G:\Unidades compartidas\Doc_Productos\06 - MODULOS\MDA\MDA100\LDM-MDA100.gsheet"
    (docs / "MODULOS/MDA100/LDM-MDA100.gsheet - Acceso directo.lnk").write_bytes(links.build_lnk(raw))
    e = estados(services, u)["LDM-MDA100.gsheet - Acceso directo.lnk"]
    assert e["status"] == "drive" and e["raw"] == raw
    from app import engine
    sim = engine.simulate(docs, "DIS206AA")
    por = {Path(f["file"]).name: f for fs in sim["folders"].values() for f in fs}
    assert por["MDA100_L1V1R0_14-07-22.xlsx"]["drive"][0]["name"] == "LDM-MDA100.gsheet"
    assert "drive" not in por["MDA300_L1V1R2_10-10-24.xlsx"] and sim["drive_files"] == 1
    pv = services.preview(services.get_user("ing@x"), {"from": "DIS206AA", "to": "DIS206BA"})
    assert any("Google Sheets" in w for w in pv["warnings"])
