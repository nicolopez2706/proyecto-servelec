"""Pruebas de .ods y .xls (el .xls necesita LibreOffice; se salta si no esta)."""
import subprocess, sys, zipfile
from pathlib import Path
import pytest
from openpyxl import Workbook, load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import engine, ods  # noqa: E402

CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" office:version="1.2">
<office:automatic-styles><style:style style:name="ce1" style:family="table-cell"/></office:automatic-styles>
<office:body><office:spreadsheet>
<table:table table:name="Items"><table:table-column table:number-columns-repeated="3"/>
<table:table-row><table:table-cell office:value-type="string"><text:p>Codigo</text:p></table:table-cell>
 <table:table-cell office:value-type="string"><text:p>Descripcion</text:p></table:table-cell>
 <table:table-cell office:value-type="string"><text:p>Cantidad</text:p></table:table-cell></table:table-row>
<table:table-row><table:table-cell table:style-name="ce1" office:value-type="string"><text:p><text:span>DIS206AA</text:span></text:p></table:table-cell>
 <table:table-cell office:value-type="string"><text:p>Disipador</text:p></table:table-cell>
 <table:table-cell office:value-type="float" office:value="4"><text:p>4</text:p></table:table-cell></table:table-row>
<table:table-row table:number-rows-repeated="2"><table:table-cell table:number-columns-repeated="3"/></table:table-row>
<table:table-row><table:table-cell office:value-type="string"><text:p>OTRO-1</text:p></table:table-cell>
 <table:table-cell table:number-columns-repeated="2" office:value-type="float" office:value="7"><text:p>7</text:p></table:table-cell></table:table-row>
<table:table-row><table:table-cell office:value-type="string"><text:p>DIS206AA</text:p></table:table-cell>
 <table:table-cell office:value-type="string"><text:p>dup</text:p></table:table-cell>
 <table:table-cell table:number-columns-repeated="1" office:value-type="float" office:value="10"><text:p>10</text:p></table:table-cell></table:table-row>
</table:table></office:spreadsheet></office:body></office:document-content>"""


def make_ods(path, content=CONTENT):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.spreadsheet", compress_type=zipfile.ZIP_STORED)
        z.writestr("content.xml", content)
        z.writestr("styles.xml", "<x/>")
        z.writestr("META-INF/manifest.xml", "<manifest/>")


def test_ods_lee_celdas_con_repeticiones(tmp_path):
    p = tmp_path / "a_L1V1R0_01-01-25.ods"; make_ods(p)
    cells = ods.OdsDoc(p).cells()
    got = {(s, c): t for s, c, t in cells}
    assert got[("Items", "A2")] == "DIS206AA" and got[("Items", "A5")] == "OTRO-1" and got[("Items", "A6")] == "DIS206AA"
    assert got[("Items", "B5")] == "7" and got[("Items", "C5")] == "7"       # columna repetida
    assert got[("Items", "C6")] == "10"


def test_ods_cambia_codigo_y_cantidad_y_conserva_formato(tmp_path):
    p = tmp_path / "a_L1V1R0_01-01-25.ods"; make_ods(p)
    d = ods.OdsDoc(p)
    ch = d.apply_change("dis206aa", "DIS206BA", 3)
    assert [c["cell"] for c in ch] == ["A2", "A6"]
    assert ch[0]["qty"] == {"before": 4.0, "after": 12.0}
    d.write_control({"rev": "L1V1R1", "what": "x", "by": "Luca", "auth": "Pri", "date": "01/01/2026", "order": "#1"})
    out = tmp_path / "out.ods"; d.save(out)
    with zipfile.ZipFile(out) as z:
        assert z.namelist()[0] == "mimetype" and z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert z.read("styles.xml") == b"<x/>"
    cells = {(s, c): t for s, c, t in ods.OdsDoc(out).cells()}
    assert cells[("Items", "A2")] == "DIS206BA" and cells[("Items", "C2")] == "12" and cells[("Items", "C6")] == "30"
    assert cells[("Items", "B5")] == "7" and cells[("Items", "C5")] == "7"       # otra fila intacta
    assert ("Control de cambios", "A2") not in cells                 # la hoja de control no entra al indice
    assert any("Control de cambios" == t.get("{urn:oasis:names:tc:opendocument:xmlns:table:1.0}name") for t in ods.OdsDoc(out).sheets)
    assert b'table:style-name="ce1"' in zipfile.ZipFile(out).read("content.xml")   # estilo de la celda conservado


def _lo(exe, fmt, out, src):
    subprocess.run([exe, "--headless", "--convert-to", fmt, "--outdir", str(out), str(src)], capture_output=True, timeout=120, check=True)


def test_ods_real_de_libreoffice_ida_y_vuelta(tmp_path):
    """Un .ods generado por LibreOffice se edita y LibreOffice lo vuelve a abrir bien."""
    exe = engine.find_soffice()
    if not exe:
        pytest.skip("sin LibreOffice")
    wb = Workbook(); ws = wb.active; ws.title = "Items"
    ws.append(["Codigo", "Descripcion", "Cantidad"]); ws.append(["DIS206AA", "Disipador", 4]); ws.append(["OTRO", "x", 1])
    ws["A2"].font = ws["A2"].font.copy(bold=True)
    x = tmp_path / "base.xlsx"; wb.save(x); _lo(exe, "ods", tmp_path, x)
    p = tmp_path / "base.ods"
    d = ods.OdsDoc(p)
    ch = d.apply_change("DIS206AA", "DIS206BA", 2)
    assert len(ch) == 1 and ch[0]["qty"]["after"] == 8
    d.write_control({"rev": "L1V1R1", "what": "x", "by": "L", "auth": "P", "date": "d", "order": "#1"})
    out = tmp_path / "chk"; out.mkdir(); d.save(out / "salida.ods")
    _lo(exe, "xlsx", out, out / "salida.ods")
    w2 = load_workbook(out / "salida.xlsx")
    assert w2.sheetnames == ["Items", "Control de cambios"]
    assert w2["Items"]["A2"].value == "DIS206BA" and w2["Items"]["C2"].value == 8 and w2["Items"]["A3"].value == "OTRO"
    assert w2["Items"]["A2"].font.bold                                   # el formato sigue
    assert w2["Control de cambios"]["A2"].value == "L1V1R1"


def test_xls_se_lee_y_se_cambia_a_xlsx(tmp_path, monkeypatch):
    if not engine.find_soffice():
        pytest.skip("sin LibreOffice")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    engine.reset_cache()
    wb = Workbook(); ws = wb.active; ws.title = "Items"
    ws.append(["Codigo", "Cantidad"]); ws.append(["DIS206AA", 5])
    src = tmp_path / "docs" / "F1"; src.mkdir(parents=True)
    wb.save(src / "t.xlsx")
    subprocess.run([engine.find_soffice(), "--headless", "--convert-to", "xls", "--outdir", str(src), str(src / "t.xlsx")], capture_output=True, timeout=120)
    (src / "t.xlsx").unlink()
    xls = src / "LDM-X_D1V1R1.xls"; (src / "t.xls").rename(xls)
    sim = engine.simulate(tmp_path / "docs", "DIS206AA")
    assert sim["total_cells"] == 1 and sim["total_files"] == 1
    doc = engine.open_doc(xls)
    assert doc.apply_change("DIS206AA", "DIS206BA", 2)[0]["qty"]["after"] == 10
    res = engine.revise_and_save(xls, doc, "rev", {"what": "w", "by": "a", "auth": "b", "date": "d", "order": "#1"})
    assert Path(res["new"]).name == "LDM-X_D1V1R2.xlsx"
    assert (src / "OBSOLETO" / "LDM-X_D1V1R1.xls").exists()
    assert load_workbook(res["new"])["Items"]["A2"].value == "DIS206BA"


def test_indice_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data")); engine.reset_cache()
    d = tmp_path / "docs" / "A"; d.mkdir(parents=True)
    make_ods(d / "x_L1V1R0_01-01-25.ods")
    s1 = engine.simulate(tmp_path / "docs", "DIS206AA")
    assert s1["total_cells"] == 2
    assert (tmp_path / "data" / "indice_celdas.json").exists()
    engine.reset_cache()
    assert engine.simulate(tmp_path / "docs", "DIS206AA")["total_cells"] == 2        # desde el disco
    make_ods(d / "x_L1V1R0_01-01-25.ods", CONTENT.replace("DIS206AA", "ZZZ"))
    assert engine.simulate(tmp_path / "docs", "DIS206AA")["total_cells"] == 0       # el cambio invalida la cache


def test_busqueda_y_uso(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data")); engine.reset_cache()
    d = tmp_path / "docs" / "A"; d.mkdir(parents=True)
    make_ods(d / "x_L1V1R0_01-01-25.ods")
    r = engine.search_text(tmp_path / "docs", "disip")
    assert r and r[0]["hits"][0]["text"] == "Disipador"
    use = engine.code_usage(tmp_path / "docs")
    assert use["DIS206AA"] == {"cells": 2, "folders": 1, "desc": "Disipador"} and "DISIPADOR" not in use
    assert "4" not in use and "7" not in use                              # cantidades no son codigos


def test_ods_conserva_namespaces_sin_uso(tmp_path):
    """xmlns:of solo aparece en el texto de las formulas: si se pierde, LibreOffice da Err:510 en todas."""
    content = CONTENT.replace('office:version="1.2">', 'xmlns:of="urn:oasis:names:tc:opendocument:xmlns:of:1.2" '
                              'xmlns:calcext="urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0" office:version="1.2">')
    content = content.replace('<text:p>4</text:p></table:table-cell></table:table-row>',
                              '<text:p>4</text:p></table:table-cell><table:table-cell table:formula="of:=[.C2]*2" office:value-type="float" office:value="8"><text:p>8</text:p></table:table-cell></table:table-row>', 1)
    p = tmp_path / "A_L1V1R0.ods"; make_ods(p, content)
    d = engine.open_doc(p); d.apply_change("DIS206AA", "DIS206ZZ"); d.save(tmp_path / "o.ods")
    xml = zipfile.ZipFile(tmp_path / "o.ods").read("content.xml").decode()
    assert 'xmlns:of="urn:oasis:names:tc:opendocument:xmlns:of:1.2"' in xml and "of:=[.C2]*2" in xml
