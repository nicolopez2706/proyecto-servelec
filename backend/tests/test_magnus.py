"""Control contra Magnus por archivo exportado."""
import base64
import io
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from test_core import env  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import magnus  # noqa: E402
from app.permissions import PermissionDenied  # noqa: E402


def _export_xlsx(path, filas, cabecera=("Código", "Descripción", "Unidad")):
    wb = Workbook()
    ws = wb.active
    ws.append(["Listado de artículos"])            # título arriba: el encabezado no está en la primera fila
    ws.append([])
    ws.append(list(cabecera))
    for f in filas:
        ws.append(list(f))
    for fila in ws.iter_rows():
        for c in fila:
            if isinstance(c.value, str) and c.value.startswith("="):
                c.data_type = "s"                      # texto que empieza con «=», no una fórmula
    wb.save(path)
    return path


@pytest.fixture()
def con_magnus(env, tmp_path, monkeypatch):  # noqa: F811
    services, u, docs, data = env
    f = _export_xlsx(tmp_path / "magnus.xlsx", [("DIS206AA", "DISIPADOR", "UN"), ("OTRO", "Tornillo hexagonal inoxidable", "UN"),
                                                ("ZZZ999", "Solo en magnus", "UN")])
    monkeypatch.setenv("MAGNUS_EXPORT", str(f))
    return services, u, docs, data, f


def test_compara_y_arma_el_informe(con_magnus):
    services, u, docs, data, f = con_magnus
    r = magnus.ejecutar(u["ing@x"])
    assert r["en_magnus"] == 3 and r["faltan"] >= 1 and r["solo_magnus"] == 1 and r["distinta"] == 1
    assert "X-1" in r["primeros_faltantes"]
    wb = load_workbook(data / "magnus" / r["informe"])
    assert wb.sheetnames == ["Resumen", "Faltan en Magnus", "Descripción distinta", "Solo en Magnus"]
    faltan = [row[0].value for row in wb["Faltan en Magnus"].iter_rows(min_row=2)]
    assert "X-1" in faltan and "DIS206AA" not in faltan
    assert [row[0].value for row in wb["Solo en Magnus"].iter_rows(min_row=2)] == ["ZZZ999"]
    assert services.list_audit(u["ger@x"])[0]["action"] == "Control contra Magnus"


def test_estado_e_informe_para_descargar(con_magnus):
    services, u, docs, data, f = con_magnus
    assert magnus.estado(u["ab@x"])["ultimo"] is None
    r = magnus.ejecutar(u["sis@x"])
    e = magnus.estado(u["ab@x"])
    assert e["configurado"] and e["archivo"] == "magnus.xlsx" and e["ultimo"]["faltan"] == r["faltan"] and e["informes"] == [r["informe"]]
    d = magnus.informe(u["ab@x"], r["informe"])
    assert load_workbook(io.BytesIO(base64.b64decode(d["base64"]))).sheetnames[0] == "Resumen"


def test_permisos_y_nombres_peligrosos(con_magnus):
    services, u, docs, data, f = con_magnus
    with pytest.raises(PermissionDenied):
        magnus.ejecutar(u["ab@x"])                       # Abastecimiento mira pero no ejecuta
    with pytest.raises(PermissionDenied):
        magnus.ejecutar(u["ger@x"])
    for malo in ("../../portal.db", "control_magnus_1.xlsx/../x", ""):
        with pytest.raises(ValueError):
            magnus.informe(u["ing@x"], malo)


def test_sin_configurar_o_sin_archivo(env, monkeypatch):
    services, u, *_ = env
    monkeypatch.delenv("MAGNUS_EXPORT", raising=False)
    assert magnus.estado(u["ing@x"])["configurado"] is False
    with pytest.raises(magnus.MagnusError, match="MAGNUS_EXPORT"):
        magnus.ejecutar(u["ing@x"])
    monkeypatch.setenv("MAGNUS_EXPORT", "/no/existe.xlsx")
    assert "No se encuentra" in magnus.estado(u["ing@x"])["error"]


def test_csv_con_punto_y_coma_y_acentos_de_windows(env, tmp_path, monkeypatch):
    services, u, *_ = env
    f = tmp_path / "exp.csv"
    f.write_bytes("Artículo;Denominación;Rubro\r\nABC123;Cable de señal;X\r\nDEF456;Ficha;Y\r\n".encode("cp1252"))
    monkeypatch.setenv("MAGNUS_EXPORT", str(f))
    assert magnus.leer_export(magnus.archivo_export()) == {"ABC123": "Cable de señal", "DEF456": "Ficha"}


def test_carpeta_usa_el_archivo_mas_nuevo_y_columnas_configurables(env, tmp_path, monkeypatch):
    import os
    services, u, *_ = env
    carpeta = tmp_path / "export"
    carpeta.mkdir()
    viejo = _export_xlsx(carpeta / "a.xlsx", [("OLD1", "viejo", "UN")])
    nuevo = _export_xlsx(carpeta / "b.xlsx", [("NEW1", "nuevo", "UN")], cabecera=("Ref interna", "Texto", "Unidad"))
    os.utime(viejo, (1, 1))
    monkeypatch.setenv("MAGNUS_EXPORT", str(carpeta))
    with pytest.raises(magnus.MagnusError, match="MAGNUS_COL_CODIGO"):         # no reconoce «Ref interna»
        magnus.leer_export(magnus.archivo_export())
    monkeypatch.setenv("MAGNUS_COL_CODIGO", "Ref interna")
    monkeypatch.setenv("MAGNUS_COL_DESC", "B")
    assert magnus.leer_export(magnus.archivo_export()) == {"NEW1": "nuevo"}


def test_descripciones_parecidas_no_se_marcan():
    assert not magnus.descripcion_distinta("Disipador", "DISIPADOR de aluminio")
    assert not magnus.descripcion_distinta("Tornillo M4 x 10", "tornillo 10 m4")
    assert not magnus.descripcion_distinta("", "lo que sea")
    assert magnus.descripcion_distinta("Disipador", "Cable de señal")


def test_el_servicio_de_n8n_puede_ejecutarlo(con_magnus):
    from app.permissions import SERVICE_ROLE
    services, u, docs, data, f = con_magnus
    svc = {"email": "n8n@x", "name": "Automatización", "role": SERVICE_ROLE}
    assert magnus.ejecutar(svc)["en_magnus"] == 3


def test_dimension_mal_declarada_no_trunca_la_lectura(env, tmp_path, monkeypatch):  # noqa: F811
    import re
    import zipfile
    f = _export_xlsx(tmp_path / "m.xlsx", [(f"C{i}", f"desc {i}", "UN") for i in range(10)])
    out = tmp_path / "m2.xlsx"
    with zipfile.ZipFile(f) as zin, zipfile.ZipFile(out, "w") as zout:
        for n in zin.namelist():
            d = zin.read(n)
            if n == "xl/worksheets/sheet1.xml":
                d = re.sub(rb'<dimension ref="[^"]*"', b'<dimension ref="A1:B3"', d)
            zout.writestr(n, d)
    assert len(magnus.leer_export(out)) == 10


def test_csv_con_comas_dentro_del_texto_y_codificacion_rara(env, tmp_path):  # noqa: F811
    f = tmp_path / "a.csv"
    f.write_text('Código;Descripción\nA1;"TORNILLO, M8, INOX, ZINCADO"\nA2;3,5 mm, doble\n', encoding="utf-8")
    assert list(magnus.leer_export(f)) == ["A1", "A2"]
    g = tmp_path / "b.csv"
    g.write_bytes(b"C\x81digo;Descripci\xf3n\nZ9;ok\n")           # no es utf-8 ni cp1252 válido en todo: no debe romper
    try:
        magnus.leer_export(g)
    except magnus.MagnusError:
        pass                                                       # mensaje claro para el usuario; lo que no puede pasar es un error interno


def test_codigos_numericos_como_decimal_y_formulas_en_el_informe(env, tmp_path, monkeypatch):  # noqa: F811
    services, u, docs, data = env
    f = _export_xlsx(tmp_path / "n.xlsx", [(1234.0, "= 5 mm x 3", "UN"), ("=HYPERLINK(\"http://x\")", "malo", "UN")])
    assert magnus.leer_export(f)["1234"] == "= 5 mm x 3"
    monkeypatch.setenv("MAGNUS_EXPORT", str(f))
    r = magnus.ejecutar(u["ing@x"])
    wb = load_workbook(data / "magnus" / r["informe"])
    for row in wb["Solo en Magnus"].iter_rows(min_row=2):
        assert all(c.data_type == "s" for c in row if isinstance(c.value, str))


def test_enlace_de_informes_anteriores_no_navega():
    html = (Path(__file__).resolve().parent.parent / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert "<a href=\"#\" data-act=\"magnusdl\"" not in html
