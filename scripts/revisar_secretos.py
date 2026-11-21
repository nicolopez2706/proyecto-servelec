"""Revisa que no se esté por subir a GitHub una clave, un token o una base de datos.

Uso (desde la carpeta del proyecto):
    python scripts/revisar_secretos.py            revisa todo lo que está en git (lo guardado y lo preparado)
    python scripts/revisar_secretos.py --hook     instala esto como revisión automática antes de cada «git commit»

Sale con código 1 si encuentra algo, y dice qué archivo y qué línea. Solo usa la biblioteca estándar.
"""
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
EJEMPLOS = ("cambiar-por", "pegar_aca", "dev-no-usar", "ejemplo", "<", "xxxx", "tu-clave")
NOMBRES_PROHIBIDOS = (".env", "secret.key", "servicio_n8n.token")
EXT_PROHIBIDAS = (".db", ".sqlite", ".sqlite3", ".pem", ".key", ".pfx")
PATRONES = [
    ("token JWT (empieza con eyJ)", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("clave privada", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("clave de Google/AWS", re.compile(r"\b(AIza[0-9A-Za-z_-]{30,}|AKIA[0-9A-Z]{16})\b")),
    ("clave puesta a mano", re.compile(r"""(?ix)\b(SECRET_KEY|N8N_WEBHOOK_SECRET|ESPERADO|PASSWORD|TOKEN|API_?KEY)\b\s*[=:]\s*(['"]?)([^\s'"#]{8,})""")),
]


def _es_ejemplo(valor):
    v = valor.lower()
    return any(e in v for e in EJEMPLOS)


def archivos():
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=RAIZ,
                         capture_output=True, check=True).stdout.decode("utf-8", "replace")
    return [RAIZ / p for p in out.split("\0") if p]


def revisar(lista=None, raiz=RAIZ):
    problemas = []
    for p in lista if lista is not None else archivos():
        rel = p.relative_to(raiz).as_posix()
        if p.name in NOMBRES_PROHIBIDOS or p.suffix.lower() in EXT_PROHIBIDAS:
            problemas.append((rel, 0, "archivo que nunca debe subirse"))
            continue
        if p.suffix.lower() in (".png", ".jpg", ".ico", ".xlsx", ".xls", ".ods", ".zip", ".lnk") or not p.is_file():
            continue
        try:
            lineas = p.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for n, linea in enumerate(lineas, 1):
            if rel.startswith("backend/tests/"):       # las pruebas usan claves inventadas a propósito
                continue
            for que, rx in PATRONES:
                for m in rx.finditer(linea):
                    valor = m.group(m.lastindex) if m.lastindex else m.group(0)
                    if que == "clave puesta a mano" and not m.group(2) and p.suffix.lower() in (".py", ".html", ".js"):
                        continue                              # en el código, solo importa si es un texto escrito entre comillas
                    if que == "clave puesta a mano" and (_es_ejemplo(valor) or valor.startswith(("$", "{", "(", "os.", "settings", "self"))
                                                          or "getenv" in linea or "environ" in linea or valor.endswith(("(", ","))):
                        continue
                    if que != "clave puesta a mano" and _es_ejemplo(m.group(0)):
                        continue
                    problemas.append((rel, n, que))
    return problemas


def instalar_hook():
    hook = RAIZ / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\npython scripts/revisar_secretos.py || python3 scripts/revisar_secretos.py\n", encoding="utf-8")
    try:
        hook.chmod(0o755)
    except OSError:
        pass
    print(f"Listo: antes de cada commit se revisa que no haya claves ({hook}).")


def main():
    if "--hook" in sys.argv:
        return instalar_hook()
    pr = revisar()
    if not pr:
        print("Sin claves ni archivos peligrosos.")
        return 0
    print("¡Cuidado! Se encontró esto, no lo subas a GitHub:")
    for rel, n, que in pr:
        print(f"  - {rel}{':' + str(n) if n else ''}  ({que})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
