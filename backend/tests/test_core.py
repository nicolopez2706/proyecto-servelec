import os, sys
from pathlib import Path
import pytest
from openpyxl import Workbook, load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    docs, data = tmp_path / "docs", tmp_path / "data"
    monkeypatch.setenv("DOCS_ROOT", str(docs)); monkeypatch.setenv("DATA_DIR", str(data))
    from app import db, services
    from app.security import hash_password
    db.init_db()
    con = db.connect()
    for e, n, r in [("ab@x", "Abast", "Abastecimiento"), ("ing@x", "Luca", "Ingeniería"),
                    ("ger@x", "Priscila", "Gerencia"), ("sis@x", "Nico", "Sistemas"), ("ger2@x", "Victoria", "Gerencia")]:
        con.execute("INSERT INTO users(email,name,role,pw_hash) VALUES(?,?,?,?)", (e, n, r, hash_password("clave1234")))
    con.commit(); con.close()

    def mk(rel, rows):
        p = docs / rel; p.parent.mkdir(parents=True, exist_ok=True)
        wb = Workbook(); ws = wb.active; ws.title = "Items"
        ws.append(["Codigo", "Descripcion", "Cantidad"])
        for r in rows: ws.append(r)
        wb.save(p); return p
    mk("MODULOS/MDA100/MDA100_L1V1R0_14-07-22.xlsx", [["DIS206AA", "Disipador", 4], ["X-1", "otro", 1]])
    mk("MODULOS/MDA200/MDA200_L1V1R0_01-02-25.xlsx", [["DIS206AA", "Disipador", 3], ["DIS206AA", "dup", 1]])
    mk("MODULOS/MDA300/MDA300_L1V1R2_10-10-24.xlsx", [["DIS206AA", "Disipador", 2]])
    mk("MODULOS/MDA400/MDA400_L1V1R0_10-10-24.xlsx", [["OTRO", "no usa", 2]])
    (docs / "MODULOS/MDA500").mkdir(parents=True); (docs / "MODULOS/MDA500/viejo_L1V1R0.ods").write_text("x")
    u = {k: services.get_user(k) for k in ("ab@x", "ing@x", "ger@x", "sis@x", "ger2@x")}
    return services, u, docs, data


def pedir(services, u, **kw):
    p = {"from": "dis206aa", "to": "DIS206BA", "rev_mode": "rev", "reason": "prueba"}
    p.update(kw)
    return services.create_order(u["ing@x"], "codigo", p)


def test_login(env):
    services, u, *_ = env
    assert services.authenticate("ING@x ", "clave1234")["role"] == "Ingeniería"
    assert services.authenticate("ing@x", "mala") is None


def test_simulacion_y_carpetas(env):
    services, u, *_ = env
    o = pedir(services, u)
    assert o["status"] == "pendiente"
    assert o["summary"]["cells"] == 4 and o["summary"]["files"] == 3 and o["summary"]["folders"] == 3
    assert o["summary"]["skipped"] == 1                      # el .ods
    assert o["params"]["from"] == "DIS206AA"


def test_validaciones(env):
    services, u, *_ = env
    with pytest.raises(ValueError): pedir(services, u, to="DIS206AA")
    with pytest.raises(ValueError): pedir(services, u, **{"from": "NOEXISTE"})
    with pytest.raises(ValueError): pedir(services, u, qty={"on": True, "factor": 0})


def test_permisos(env):
    services, u, *_ = env
    from app.permissions import PermissionDenied
    o = pedir(services, u)
    with pytest.raises(PermissionDenied): services.approve(u["ab@x"], o["id"])         # Abastecimiento no aprueba
    with pytest.raises(PermissionDenied): services.approve(u["ing@x"], o["id"])        # ni se autoaprueba
    with pytest.raises(PermissionDenied): services.list_audit(u["ing@x"])              # Ingenieria sin auditoria
    with pytest.raises(PermissionDenied): services.list_users(u["ger@x"])
    assert services.list_audit(u["ger@x"])


def test_aprobar_eligiendo_carpetas_y_revision(env):
    services, u, docs, _ = env
    o = pedir(services, u)
    folders = list(o["sim"]["folders"])
    elegidas = [f for f in folders if not f.endswith("MDA300")]
    r = services.approve(u["ger@x"], o["id"], "ok", elegidas)
    assert r["status"] == "parcial"                          # el .ods quedó pendiente
    assert any("sacó 1" in e["text"] for e in r["events"])
    assert r["params"]["scope_orig"] == folders
    # MDA300 no se tocó
    assert (docs / "MODULOS/MDA300/MDA300_L1V1R2_10-10-24.xlsx").exists()
    # MDA100: nuevo nombre R1, original en OBSOLETO, celdas cambiadas, hoja de control
    new = [p for p in (docs / "MODULOS/MDA100").glob("*.xlsx")]
    assert len(new) == 1 and "L1V1R1" in new[0].name
    assert (docs / "MODULOS/MDA100/OBSOLETO/MDA100_L1V1R0_14-07-22.xlsx").exists()
    wb = load_workbook(new[0])
    assert wb["Items"]["A2"].value == "DIS206BA"
    ctl = wb["Control de cambios"]
    row = [c.value for c in ctl[2]]
    assert row[0] == "L1V1R1" and row[3] == "Luca" and row[4] == "Priscila" and row[6] == f"#{o['id']}"
    assert "DIS206AA por DIS206BA en 1 celdas" in row[1]


def test_version_nueva_deja_la_anterior(env):
    services, u, docs, _ = env
    o = pedir(services, u, rev_mode="ver")
    services.approve(u["ger@x"], o["id"])
    names = sorted(p.name[:22] for p in (docs / "MODULOS/MDA100").glob("*.xlsx"))
    assert any("L1V1R0" in n for n in names) and any("L1V2R0" in n for n in names)
    assert not (docs / "MODULOS/MDA100/OBSOLETO").exists()


def test_cantidades(env):
    services, u, docs, _ = env
    o = pedir(services, u, qty={"on": True, "factor": 2})
    services.approve(u["ger@x"], o["id"])
    f = next((docs / "MODULOS/MDA100").glob("*R1*.xlsx"))
    ws = load_workbook(f)["Items"]
    assert ws["A2"].value == "DIS206BA" and ws["C2"].value == 8     # 4 x2
    assert ws["C3"].value == 1                                      # otra fila no cambia


def test_revertir(env):
    services, u, docs, _ = env
    antes = {str(p.relative_to(docs)): p.read_bytes() for p in docs.rglob("*") if p.is_file()}
    o = pedir(services, u)
    services.approve(u["ger@x"], o["id"])
    from app.permissions import PermissionDenied
    with pytest.raises(PermissionDenied): services.revert(u["ing@x"], o["id"])
    r = services.revert(u["ger@x"], o["id"])
    assert r["status"] == "revertido"
    despues = {str(p.relative_to(docs)): p.read_bytes() for p in docs.rglob("*") if p.is_file()}
    assert despues == antes                                   # idéntico, sin OBSOLETO ni archivos nuevos


def test_archivo_abierto_se_omite_y_queda_pendiente(env):
    services, u, docs, _ = env
    (docs / "MODULOS/MDA200/~$MDA200_L1V1R0_01-02-25.xlsx").write_text("lock")
    o = pedir(services, u)
    assert o["summary"]["files"] == 2 and o["summary"]["skipped"] == 2
    r = services.approve(u["ger@x"], o["id"])
    assert (docs / "MODULOS/MDA200/MDA200_L1V1R0_01-02-25.xlsx").exists()     # sin tocar


def test_rechazo_y_estado(env):
    services, u, *_ = env
    o = pedir(services, u)
    r = services.reject(u["ger@x"], o["id"], "no va")
    assert r["status"] == "rechazado"
    with pytest.raises(ValueError): services.approve(u["ger2@x"], o["id"])


def test_carpeta_invalida(env):
    services, u, *_ = env
    o = pedir(services, u)
    with pytest.raises(ValueError): services.approve(u["ger@x"], o["id"], "", ["NO/EXISTE"])
    with pytest.raises(ValueError): services.approve(u["ger@x"], o["id"], "", [])


def test_limpieza_obsoletos(env):
    services, u, docs, _ = env
    d = docs / "MODULOS/MDA300"
    (d / "MDA300_L1V1R0_10-10-24.xlsx").write_bytes((d / "MDA300_L1V1R2_10-10-24.xlsx").read_bytes())
    (d / "MDA300_L1V1R1_10-10-24.xlsx").write_bytes(b"r1")
    r = services.run_cleanup(u["ing@x"], dry_run=True)
    assert len(r["moved"]) == 2 and (d / "MDA300_L1V1R0_10-10-24.xlsx").exists()
    services.run_cleanup(u["ing@x"])
    assert sorted(p.name for p in d.glob("*.xlsx")) == ["MDA300_L1V1R2_10-10-24.xlsx"]
    assert len(list((d / "OBSOLETO").glob("*.xlsx"))) == 2


def test_dos_aprobaciones_a_la_vez_ejecutan_una_sola_vez(env):
    """Dos personas que aprueban el mismo pedido en el mismo instante: solo una lo ejecuta."""
    import threading
    services, u, docs, _ = env
    o = pedir(services, u)
    res = []

    def ir(email):
        try:
            res.append(("ok", services.approve(u[email], o["id"])["status"]))
        except Exception as e:
            res.append(("error", str(e)))

    hilos = [threading.Thread(target=ir, args=(e,)) for e in ("ger@x", "ger@x", "ger@x")]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert sum(1 for k, _ in res if k == "ok") == 1, res
    assert all("ya no está esperando" in m for k, m in res if k == "error"), res
    con = services.connect()
    n = con.execute("SELECT COUNT(*) c FROM events WHERE order_id=? AND text LIKE 'Aprobó el pedido%'", (o["id"],)).fetchone()["c"]
    con.close()
    assert n == 1


def test_claim_solo_deja_pasar_a_uno(env):
    """Aunque dos personas hayan leído «pendiente» antes de decidir, el segundo cambio de estado se rechaza."""
    import pytest
    services, u, docs, _ = env
    o = pedir(services, u)
    con = services.connect()
    try:
        services._claim(con, o["id"], "ejecutando", decided_by="a@x", decided_at="ahora")
        con.commit()
        with pytest.raises(ValueError, match="ya no está esperando"):
            services._claim(con, o["id"], "ejecutando", decided_by="b@x", decided_at="ahora")
        assert con.execute("SELECT decided_by FROM orders WHERE id=?", (o["id"],)).fetchone()["decided_by"] == "a@x"
    finally:
        con.close()
