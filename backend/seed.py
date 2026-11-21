"""Crea la base y los usuarios iniciales. Cada usuario recibe una clave temporal DISTINTA, que se muestra una sola vez
y que la persona tiene que cambiar en su primer ingreso (el portal no la deja seguir hasta que lo haga).

Uso (desde la carpeta backend, con el entorno virtual activado):
    python seed.py                   crea los usuarios que falten, cada uno con su clave temporal
    python seed.py --exigir-cambio   obliga a TODOS los usuarios ya existentes a cambiar su clave en el próximo ingreso
                                     (útil si la base viene de cuando todos tenían la misma clave inicial)
    python seed.py --solo-correos    muestra los usuarios que hay, sin tocar nada

Si la persona pierde su clave: python reset_clave.py <correo>  (la clave anterior no se puede ver ni recuperar)."""
import os
import sys

from app.db import connect, init_db
from app.permissions import SERVICE_ROLE
from app.security import hash_password
from app.services import CLAVES_DEBILES, clave_temporal

USERS = [
    ("abastecimiento@servelec.com.ar", "Compras y Abastecimiento", "Abastecimiento"),
    ("l.bazzoli@servelec.com.ar", "Luca Bazzoli", "Ingeniería"),
    ("p.delfino@servelec.com.ar", "Priscila Delfino", "Gerencia"),
    ("v.delfino@servelec.com.ar", "Victoria Delfino", "Gerencia"),
    ("n.lopez@servelec.com.ar", "Nicolas Lopez", "Sistemas"),
]


def sembrar(clave_fija=None):
    """Crea los usuarios que no existen. Devuelve [(correo, area, clave_temporal)] solo de los creados."""
    init_db()
    creados = []
    con = connect()
    try:
        for email, name, role in USERS:
            if not con.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                pw = clave_fija or clave_temporal()
                con.execute("INSERT INTO users(email,name,role,pw_hash,must_change) VALUES(?,?,?,?,1)",
                            (email, name, role, hash_password(pw)))
                creados.append((email, role, pw))
        con.commit()
    finally:
        con.close()
    return creados


def exigir_cambio():
    """Marca a todas las personas (no al usuario de servicio de n8n) para que cambien la clave al ingresar."""
    init_db()
    con = connect()
    try:
        n = con.execute("UPDATE users SET must_change=1 WHERE role<>?", (SERVICE_ROLE,)).rowcount
        con.commit()
        return n
    finally:
        con.close()


if __name__ == "__main__":
    init_db()
    if "--solo-correos" in sys.argv:
        con = connect()
        for r in con.execute("SELECT email, role, active, must_change FROM users ORDER BY role, email"):
            print(f"{r['email']:40} {r['role']:16} {'activo' if r['active'] else 'inactivo':9} {'debe cambiar la clave' if r['must_change'] else ''}")
        sys.exit(0)
    if "--exigir-cambio" in sys.argv:
        print(f"Listo: {exigir_cambio()} usuario(s) tendrán que cambiar su clave en el próximo ingreso.")
        sys.exit(0)
    fija = os.getenv("SEED_PASSWORD") or None
    if fija and fija.lower() in CLAVES_DEBILES:
        print("Aviso: SEED_PASSWORD es una clave conocida (por ejemplo la de ejemplo vieja): se ignora y cada usuario recibe una clave al azar.")
        fija = None
    creados = sembrar(fija)
    if not creados:
        print("No había usuarios para crear (ya existen todos). No se cambió ninguna clave.")
    else:
        print("Usuarios creados. Cada uno tiene su CLAVE TEMPORAL (se muestra solo ahora; anotala y entregala a cada persona):\n")
        for email, role, pw in creados:
            print(f"  {email:40} {role:16} {pw}")
        print("\nAl ingresar por primera vez el portal les pide una clave nueva. Si alguien la pierde: python reset_clave.py <correo>")
