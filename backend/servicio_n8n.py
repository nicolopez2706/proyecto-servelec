"""Crea el usuario de servicio para n8n y el asistente, y entrega su token de acceso.

El usuario tiene el area "Automatización": puede mirar, simular, pedir cambios y avisar codigos que faltan.
NUNCA aprueba, revierte ni administra: eso lo hacen siempre personas.

Uso (desde la carpeta backend, con el entorno virtual activado):
    python servicio_n8n.py                 crea el usuario (si falta) y muestra un token valido 365 dias
    python servicio_n8n.py --dias 90       token con otra duracion
    python servicio_n8n.py --desactivar    lo apaga: ningun token suyo vuelve a servir (para volver a usarlo, correr sin opciones)
El token se pega en n8n como encabezado:  Authorization: Bearer <token>
Cada vez que se corre se muestra un token nuevo; los anteriores siguen valiendo hasta vencer (o hasta desactivar el usuario)."""
import argparse
import secrets

from app.db import connect, init_db
from app.permissions import SERVICE_ROLE
from app.security import hash_password, make_token

EMAIL = "n8n@servelec.local"
NAME = "Automatización (n8n)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=365)
    ap.add_argument("--desactivar", action="store_true")
    a = ap.parse_args()
    init_db()
    con = connect()
    try:
        row = con.execute("SELECT * FROM users WHERE email=?", (EMAIL,)).fetchone()
        if a.desactivar:
            if row:
                con.execute("UPDATE users SET active=0 WHERE email=?", (EMAIL,))
                con.commit()
            print("Usuario de servicio desactivado. Sus tokens ya no sirven.")
            return
        if not row:
            con.execute("INSERT INTO users(email,name,role,pw_hash) VALUES(?,?,?,?)",
                        (EMAIL, NAME, SERVICE_ROLE, hash_password(secrets.token_urlsafe(32))))
            print("Usuario creado:", EMAIL)
        else:
            con.execute("UPDATE users SET active=1, role=? WHERE email=?", (SERVICE_ROLE, EMAIL))
        con.commit()
    finally:
        con.close()
    print(f"Token válido {a.dias} días (copialo entero, es una sola línea):\n")
    print(make_token(EMAIL, hours=24 * a.dias))
    print("\nEn n8n (nodo HTTP Request) usalo como encabezado:  Authorization = Bearer <token>")


if __name__ == "__main__":
    main()
