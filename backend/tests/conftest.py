"""Las pruebas NUNCA deben usar la configuración real (.env): si el .env tiene N8N_WEBHOOK_URL, cada pedido de prueba
mandaría un aviso (y un correo) de verdad. Antes de cada prueba se limpia todo lo que viene del entorno."""
import queue

import pytest

# configuración que, si está en el .env real, cambiaría el resultado de las pruebas o tocaría cosas de verdad
_LIMPIAR = ("N8N_WEBHOOK_URL", "N8N_WEBHOOK_SECRET", "N8N_DELAY", "PORTAL_URL", "SECRET_KEY", "MAGNUS_EXPORT", "MAGNUS_COL_CODIGO",
            "MAGNUS_COL_DESC", "READ_ONLY", "IGNORE_LOCKS", "PERMITIR_PERDIDA_XLSX", "LINK_MAP", "EXTRA_ROOTS", "EXCLUDE_DIRS",
            "SCAN_TTL", "LOGIN_MAX_FAILS", "LOGIN_LOCK_MINUTES", "SOFFICE_PATH", "TOKEN_HOURS")


@pytest.fixture(autouse=True)
def entorno_aislado(monkeypatch, tmp_path_factory):
    for k in _LIMPIAR:
        monkeypatch.delenv(k, raising=False)
    # carpetas por defecto en un lugar temporal: ninguna prueba puede tocar los datos ni las planillas reales
    base = tmp_path_factory.mktemp("aislado")
    monkeypatch.setenv("DATA_DIR", str(base / "data"))
    monkeypatch.setenv("DOCS_ROOT", str(base / "docs"))
    yield
    from app import events
    while True:                                   # avisos que hayan quedado en cola no deben salir después de la prueba
        try:
            events._Q.get_nowait()
        except queue.Empty:
            break
