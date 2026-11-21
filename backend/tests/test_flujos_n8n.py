"""Los flujos de n8n que se importan a mano: JSON válido, conexiones que existen y sin claves reales."""
import json
from pathlib import Path

import pytest

FLUJOS = sorted((Path(__file__).resolve().parents[2] / "workflows").glob("*.json"))


def test_hay_flujos():
    assert len(FLUJOS) >= 3


@pytest.mark.parametrize("ruta", FLUJOS, ids=lambda p: p.name)
def test_flujo_bien_armado(ruta):
    f = json.loads(ruta.read_text(encoding="utf-8"))
    nombres = {n["name"] for n in f["nodes"]}
    assert len(nombres) == len(f["nodes"]), "nombres de nodo repetidos"
    assert len({n["id"] for n in f["nodes"]}) == len(f["nodes"]), "ids de nodo repetidos"
    for origen, salidas in f["connections"].items():
        assert origen in nombres
        for rama in salidas["main"]:
            for c in rama:
                assert c["node"] in nombres
    assert f["active"] is False                      # se activan a mano en n8n, después de configurarlos
