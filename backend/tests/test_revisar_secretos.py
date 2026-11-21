"""La revisión previa al commit encuentra claves de verdad y no se queja de los ejemplos ni del código normal."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("revisar_secretos", Path(__file__).resolve().parents[2] / "scripts" / "revisar_secretos.py")
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)


def _rev(tmp_path, nombre, texto):
    f = tmp_path / nombre
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(texto, encoding="utf-8")
    return rs.revisar([f], raiz=tmp_path)


def test_encuentra_una_clave_real(tmp_path):
    assert _rev(tmp_path, "workflows/a.json", '"jsCode": "const ESPERADO = \'k8Jd92hf83hf92hd\';"')
    assert _rev(tmp_path, ".env", "SECRET_KEY=abcdefghijklmnop1234")
    assert _rev(tmp_path, "x.md", "token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhIn0abcdef.firmafirmafirma1")


def test_archivos_que_nunca_se_suben(tmp_path):
    assert _rev(tmp_path, "backend/data/portal.db", "x")
    assert _rev(tmp_path, "backend/data/secret.key", "x")


def test_ejemplos_y_codigo_normal_pasan(tmp_path):
    assert not _rev(tmp_path, "backend/.env.example", "# N8N_WEBHOOK_SECRET=cambiar-por-una-clave-larga")
    assert not _rev(tmp_path, "a.py", "token = auth[7:] if auth else None\npassword = password or clave_temporal()")
    assert not _rev(tmp_path, "b.json", '"x": "const ESPERADO = \'cambiar-por-una-clave-larga\';"')


def test_el_proyecto_actual_esta_limpio():
    try:
        lista = rs.archivos()
    except Exception:                      # sin git instalado o fuera de un repositorio
        import pytest
        pytest.skip("no hay git")
    assert rs.revisar(lista) == []
