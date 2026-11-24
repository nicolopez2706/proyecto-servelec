import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_seed_por_consola_no_recrea_usuarios_borrados(tmp_path):
    """Correr el instalador sobre una base que ya tiene usuarios no debe traer de vuelta a los que se borraron a propósito."""
    back = Path(__file__).resolve().parent.parent
    env = {**os.environ, "DATA_DIR": str(tmp_path / "data"), "SEED_PASSWORD": ""}

    def correr(*args):
        return subprocess.run([sys.executable, "seed.py", *args], cwd=back, env=env, capture_output=True, text=True, timeout=120)

    def cuantos(correo):
        con = sqlite3.connect(tmp_path / "data" / "portal.db")
        try:
            return con.execute("SELECT COUNT(*) FROM users WHERE email=?", (correo,)).fetchone()[0]
        finally:
            con.close()

    borrado = "l.bazzoli@servelec.com.ar"
    assert "Usuarios creados" in correr().stdout                      # base nueva: crea la lista inicial
    con = sqlite3.connect(tmp_path / "data" / "portal.db")
    con.execute("DELETE FROM users WHERE email=?", (borrado,))
    con.commit()
    con.close()
    r = correr()
    assert "ya tiene" in r.stdout and "Usuarios creados" not in r.stdout
    assert cuantos(borrado) == 0                                      # el borrado no reaparece
    assert borrado in correr("--completar").stdout and cuantos(borrado) == 1   # a pedido, completa los que faltan
