"""Respaldo y restauración."""
import sqlite3
import sys
from pathlib import Path

import pytest
from test_core import env  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import respaldo  # noqa: E402


def test_respaldo_y_restauracion(env, tmp_path, monkeypatch):  # noqa: F811
    services, u, docs, data = env
    (data / "secret.key").write_text("clave-secreta-de-prueba")
    n8n = tmp_path / "n8n"
    n8n.mkdir()
    (n8n / "config").write_text('{"encryptionKey":"x"}')
    sqlite3.connect(n8n / "database.sqlite").executescript("CREATE TABLE t(a); INSERT INTO t VALUES(1);")
    dest = tmp_path / "copias"
    carpeta, hecho = respaldo.hacer_respaldo(dest, n8n_dir=n8n)
    assert (carpeta / "portal.db").exists() and (carpeta / "secret.key").exists() and (carpeta / "n8n" / "config").exists()
    assert sqlite3.connect(carpeta / "n8n" / "database.sqlite").execute("SELECT a FROM t").fetchone() == (1,)
    assert "LEEME.txt" in {p.name for p in carpeta.iterdir()} and any("n8n" in h for h in hecho)
    # se rompe algo después del respaldo…
    con = services.connect()
    con.execute("DELETE FROM users WHERE email='ing@x'")
    con.commit()
    con.close()
    (data / "secret.key").write_text("otra")
    respaldo.restaurar(carpeta)
    assert services.get_user("ing@x") is not None
    assert (data / "secret.key").read_text() == "clave-secreta-de-prueba"
    assert list(data.glob("secret.key.antes_*"))               # lo anterior no se pierde


def test_solo_quedan_los_ultimos(env, tmp_path):  # noqa: F811
    import datetime as dt
    dest = tmp_path / "copias"
    for i in range(5):
        respaldo.hacer_respaldo(dest, mantener=3, ahora=dt.datetime(2026, 1, 1 + i, 9, 0, 0))
    assert sorted(p.name for p in dest.iterdir()) == ["portal_2026-01-03_090000", "portal_2026-01-04_090000", "portal_2026-01-05_090000"]


def test_restaurar_algo_que_no_es_un_respaldo(env, tmp_path):  # noqa: F811
    with pytest.raises(FileNotFoundError):
        respaldo.restaurar(tmp_path)


def test_n8n_inexistente_avisa(env, tmp_path):  # noqa: F811
    with pytest.raises(FileNotFoundError):
        respaldo.hacer_respaldo(tmp_path / "c", n8n_dir=tmp_path / "no")
