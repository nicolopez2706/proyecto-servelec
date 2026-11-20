import pytest
from openpyxl import Workbook
from app import engine


def _libro(path, hojas):
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook(); wb.remove(wb.active)
    for nombre, filas in hojas.items():
        ws = wb.create_sheet(nombre)
        for f in filas:
            ws.append(f)
    wb.save(path)


@pytest.fixture()
def docs(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data")); engine.reset_cache()
    return tmp_path / "docs"


def test_los_codigos_salen_de_la_columna_de_codigos(docs):
    _libro(docs / "A" / "MCD_L1V1R0_01-01-25.xlsx", {"Hoja1": [
        ["Listado de Materiales:"], ["Cód. Doc:", "L1V1R0"], [],
        ["Item", "Referencia", "Detalle", "Código Magnus", "Fabricante", "Cantidad"],
        [1, "K1,K2", "Diodo rectificador", "300UR160", "Zoloda", 7],
        [2, "X1", "Extremo para riel", "EKN1", "Zoloda", 2],     # EKN1 parece referencia, pero esta en la columna de codigos
        [3, "Q1", "Servicio de armado", "ARMADO", "", 1],
        [4, "T1", "Borne 2,5", "BPN-2.5", "", 3]]})
    u = engine.code_usage(docs)
    assert {"300UR160", "EKN1", "ARMADO", "BPN-2.5"} <= set(u)
    for basura in ("K1", "K1,K2", "X1", "Q1", "T1", "L1V1R0"):
        assert basura not in u
    assert u["300UR160"]["desc"] == "Diodo rectificador"             # la descripcion viene de la columna «Detalle»
    assert u["EKN1"]["desc"] == "Extremo para riel" and u["EKN1"]["cells"] == 1


def test_sin_encabezado_de_codigos_se_descarta_lo_que_no_es_codigo(docs):
    _libro(docs / "B" / "X_L1V1R0_01-01-25.xlsx", {"Hoja1": [
        ["DIS206AA", "Disipador"], ["K1", "rele"], ["L1V1R2", "version"], ["PDS_V2R1", "otra"], ["TR1", "trafo"],
        ["MCDS110V100AAA", "Modulo"], ["1N4007", "Diodo"]]})
    u = engine.code_usage(docs)
    assert {"DIS206AA", "MCDS110V100AAA", "1N4007"} <= set(u)
    assert not ({"K1", "L1V1R2", "PDS_V2R1", "TR1"} & set(u))


def test_columna_de_referencias_nunca_es_codigo(docs):
    _libro(docs / "C" / "Y_L1V1R0_01-01-25.xlsx", {"Hoja1": [
        ["Item", "Referencias", "Código"], [1, "ABC123XYZ", "ZZZ-001"]]})
    u = engine.code_usage(docs)
    assert "ZZZ-001" in u and "ABC123XYZ" not in u


def test_la_busqueda_del_codigo_no_cambia(docs):
    """El filtro solo afecta la lista de codigos: buscar y simular siguen encontrando cualquier celda igual al codigo."""
    _libro(docs / "D" / "Z_L1V1R0_01-01-25.xlsx", {"Hoja1": [["Código"], ["K1"]]})
    assert engine.simulate(docs, "K1")["total_cells"] == 1
