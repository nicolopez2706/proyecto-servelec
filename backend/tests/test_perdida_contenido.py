"""El portal no debe guardar un .xlsx si al volver a escribirlo se perdería contenido (imágenes, formas, gráficos...)."""
import shutil
import sys
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XlImage
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import engine  # noqa: E402
from app.config import settings  # noqa: E402

ENTRY = {"what": "x", "why": "y", "by": "a", "auth": "b", "date": "01/01/2026", "order": "#1"}


def _xlsx(path, imagen=False):
    wb = Workbook()
    ws = wb.active
    ws.append(["Código", "Cantidad"])
    ws.append(["AAA111", 2])
    if imagen:
        png = path.parent / "logo.png"
        Image.new("RGB", (10, 10), "red").save(png)
        ws.add_image(XlImage(str(png)), "E2")
    wb.save(path)
    return path


def _con_forma(path):
    """Agrega un cuadro de texto a un dibujo del archivo, como los que arma Excel y openpyxl descarta."""
    dib = (b'<?xml version="1.0"?><xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing">'
           b'<xdr:twoCellAnchor><xdr:sp><xdr:nvSpPr/></xdr:sp></xdr:twoCellAnchor></xdr:wsDr>')
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w") as zout:
        for n in zin.namelist():
            zout.writestr(n, zin.read(n))
        zout.writestr("xl/drawings/drawing1.xml", dib)
    shutil.move(tmp, path)


def _cambiar(path):
    doc = engine.open_doc(path)
    doc.apply_change("AAA111", "BBB222")
    return engine.revise_and_save(path, doc, "rev", ENTRY)


def test_con_imagen_se_conserva(tmp_path):
    p = _xlsx(tmp_path / "a_L1V1R0.xlsx", imagen=True)
    res = _cambiar(p)
    assert Path(res["new"]).exists()
    assert engine._contenido_xlsx(res["new"]).get("imágenes") == 1


def test_sin_pillow_no_se_pisa_el_archivo(tmp_path, monkeypatch):
    p = _xlsx(tmp_path / "a_L1V1R0.xlsx", imagen=True)
    import openpyxl.reader.drawings as dr
    monkeypatch.setattr(dr, "PILImage", None)             # como si Pillow no estuviera instalado
    with pytest.raises(ValueError, match="imágenes"):
        _cambiar(p)
    assert p.exists()                                      # el original sigue en su lugar
    assert not list(tmp_path.glob("*R1*")) and not list(tmp_path.glob(".tmp_*"))
    assert not (tmp_path / "OBSOLETO").exists()


def test_una_forma_hace_que_no_se_toque(tmp_path):
    p = _xlsx(tmp_path / "b_L1V1R0.xlsx")
    _con_forma(p)
    assert engine._contenido_xlsx(p) == {"dibujos y formas": 1}
    with pytest.raises(ValueError, match="dibujos y formas"):
        _cambiar(p)
    assert p.exists() and not list(tmp_path.glob("*R1*"))


def test_se_puede_permitir_la_perdida(tmp_path, monkeypatch):
    p = _xlsx(tmp_path / "c_L1V1R0.xlsx")
    _con_forma(p)
    monkeypatch.setenv("PERMITIR_PERDIDA_XLSX", "1")
    assert Path(_cambiar(p)["new"]).exists()


def test_sin_contenido_especial_no_molesta(tmp_path):
    p = _xlsx(tmp_path / "d_L1V1R0.xlsx")
    assert engine._contenido_xlsx(p) == {}
    assert Path(_cambiar(p)["new"]).exists()
