"""Restablece la clave de un usuario desde la computadora donde corre el portal (por si quien administra olvidó la suya).
Pone una clave temporal nueva, desbloquea el ingreso y obliga a cambiarla en el próximo ingreso.

Uso (carpeta backend, entorno virtual activado):
    python reset_clave.py n.lopez@servelec.com.ar
La clave anterior no se puede ver ni recuperar (se guarda cifrada); esto solo pone una nueva."""
import datetime as dt
import sys

from app.db import connect, init_db
from app.security import hash_password
from app.services import clave_temporal


def restablecer(email):
    init_db()
    email = email.strip().lower()
    nueva = clave_temporal()
    con = connect()
    try:
        if not con.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise SystemExit(f"No existe el usuario {email}. Usuarios cargados: python seed.py --solo-correos")
        con.execute("UPDATE users SET pw_hash=?, must_change=1 WHERE email=?", (hash_password(nueva), email))
        con.execute("DELETE FROM login_fail WHERE email=?", (email,))
        con.execute("INSERT INTO audit(at,by,action,target,detail) VALUES(?,?,?,?,?)",
                    (dt.datetime.now().isoformat(timespec="seconds"), "(consola)", "Restableció la clave de", email, ""))
        con.commit()
    finally:
        con.close()
    return nueva


if __name__ == "__main__":
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        raise SystemExit("Uso: python reset_clave.py correo@servelec.com.ar")
    print(f"Clave temporal para {sys.argv[1]}:  {restablecer(sys.argv[1])}\nAl ingresar, el portal le pide una clave nueva.")
