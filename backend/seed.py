"""Crea la base y los usuarios de ejemplo (los mismos de la demo). Uso: python seed.py"""
import os
from app.db import init_db, connect
from app.security import hash_password

USERS = [
    ("abastecimiento@servelec.com.ar", "Compras y Abastecimiento", "Abastecimiento"),
    ("l.bazzoli@servelec.com.ar", "Luca Bazzoli", "Ingeniería"),
    ("p.delfino@servelec.com.ar", "Priscila Delfino", "Gerencia"),
    ("v.delfino@servelec.com.ar", "Victoria Delfino", "Gerencia"),
    ("n.lopez@servelec.com.ar", "Nicolas Lopez", "Sistemas"),
]

if __name__ == "__main__":
    init_db()
    pw = os.getenv("SEED_PASSWORD", "cambiar123")
    con = connect()
    for email, name, role in USERS:
        if not con.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            con.execute("INSERT INTO users(email,name,role,pw_hash) VALUES(?,?,?,?)", (email, name, role, hash_password(pw)))
            print("creado", email, role)
    con.commit(); con.close()
    print("Listo. Clave inicial de todos:", pw, "(cambiala)")
