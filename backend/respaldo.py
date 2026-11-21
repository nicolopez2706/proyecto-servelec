"""Respaldo de lo que NO se puede volver a armar: la base del portal (usuarios, pedidos, auditoría), el archivo .env,
la clave secreta de las sesiones y, si se indica, los datos de n8n (incluida su clave de cifrado).

Uso (carpeta backend, entorno virtual activado):
    python respaldo.py DESTINO                         copia a DESTINO\\portal_AAAA-MM-DD_HHMMSS
    python respaldo.py DESTINO --n8n C:\\Users\\X\\.n8n   incluye también n8n
    python respaldo.py DESTINO --con-pedidos            incluye las copias de planillas que guarda cada pedido (puede ser grande)
    python respaldo.py --restaurar DESTINO\\portal_...  vuelve la base del portal a ese respaldo (con el portal APAGADO)

DESTINO debería ser otro disco o una carpeta de red: si el respaldo está en la misma computadora, no sirve si se rompe.
Los respaldos contienen claves: dejarlos en un lugar con acceso restringido. Se conservan los últimos 14 (--mantener N)."""
import argparse
import datetime as dt
import shutil
import sqlite3
import sys
from pathlib import Path

from app.config import BASE, settings

PREFIJO = "portal_"


def _copiar_sqlite(origen, destino):
    """Copia una base SQLite aunque esté en uso (API de respaldo de SQLite) y comprueba que la copia quedó sana."""
    src = sqlite3.connect(f"file:{origen}?mode=ro", uri=True)
    dst = sqlite3.connect(destino)
    try:
        src.backup(dst)
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        dst.close()
        src.close()
    if ok != "ok":
        raise RuntimeError(f"La copia de {Path(origen).name} salió dañada: {ok}")


def hacer_respaldo(destino, n8n_dir=None, con_pedidos=False, mantener=14, ahora=None):
    destino = Path(destino)
    ahora = ahora or dt.datetime.now()
    if n8n_dir and not Path(n8n_dir).is_dir():
        raise FileNotFoundError(f"No existe la carpeta de n8n: {n8n_dir}")
    carpeta = destino / f"{PREFIJO}{ahora:%Y-%m-%d_%H%M%S}"
    carpeta.mkdir(parents=True)
    hecho = []
    if settings.db_path.exists():
        _copiar_sqlite(settings.db_path, carpeta / "portal.db")
        hecho.append("portal.db (usuarios, pedidos, catálogo, auditoría)")
    for nombre, ruta in ((".env", BASE / ".env"), ("secret.key", settings.data_dir / "secret.key")):    # el índice de celdas se rearma solo
        if ruta.exists():
            shutil.copy2(ruta, carpeta / nombre)
            hecho.append(nombre)
    if settings.magnus_dir.exists():
        shutil.copytree(settings.magnus_dir, carpeta / "magnus")
        hecho.append("informes de Magnus")
    if n8n_dir:
        n8n_dir = Path(n8n_dir)
        (carpeta / "n8n").mkdir()
        if (n8n_dir / "database.sqlite").exists():
            _copiar_sqlite(n8n_dir / "database.sqlite", carpeta / "n8n" / "database.sqlite")
            hecho.append("n8n: flujos y credenciales (database.sqlite)")
        if (n8n_dir / "config").exists():
            shutil.copy2(n8n_dir / "config", carpeta / "n8n" / "config")
            hecho.append("n8n: clave de cifrado (config) — sin ella las credenciales no se pueden leer")
    if con_pedidos and settings.backup_dir.exists():
        shutil.copytree(settings.backup_dir, carpeta / "respaldos_de_pedidos")
        hecho.append("copias de planillas de cada pedido")
    (carpeta / "LEEME.txt").write_text(
        f"Respaldo del portal SERVELEC hecho el {ahora:%d/%m/%Y %H:%M}.\n\nContiene:\n- " + "\n- ".join(hecho)
        + "\n\nContiene claves: no lo dejes en un lugar abierto a todos.\nPara volver atrás: apagar el portal y correr "
          "python respaldo.py --restaurar \"esta carpeta\".\n", encoding="utf-8")
    anteriores = sorted(p for p in destino.iterdir() if p.is_dir() and p.name.startswith(PREFIJO))
    for viejo in anteriores[:-mantener] if mantener > 0 else []:
        shutil.rmtree(viejo, ignore_errors=True)
    return carpeta, hecho


def restaurar(carpeta):
    """Vuelve la base del portal (y el .env / clave secreta si estaban) al estado del respaldo. Deja lo anterior al lado."""
    carpeta = Path(carpeta)
    if not (carpeta / "portal.db").exists():
        raise FileNotFoundError(f"{carpeta} no tiene portal.db: no parece un respaldo del portal.")
    sello = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    pares = [(carpeta / "portal.db", settings.db_path), (carpeta / "secret.key", settings.data_dir / "secret.key"),
             (carpeta / ".env", BASE / ".env")]
    vueltos = []
    for origen, destino in pares:
        if not origen.exists():
            continue
        if destino.exists():
            shutil.copy2(destino, destino.with_name(destino.name + f".antes_{sello}"))
        shutil.copy2(origen, destino)
        for extra in ("-wal", "-shm"):                      # restos de la base anterior que ya no corresponden
            Path(str(destino) + extra).unlink(missing_ok=True)
        vueltos.append(destino.name)
    return vueltos


def main(argv=None):
    ap = argparse.ArgumentParser(description="Respaldo y restauración del portal SERVELEC")
    ap.add_argument("destino", nargs="?", help="carpeta donde se guarda el respaldo")
    ap.add_argument("--n8n", help="carpeta de datos de n8n (por ejemplo C:\\Users\\X\\.n8n)")
    ap.add_argument("--con-pedidos", action="store_true")
    ap.add_argument("--mantener", type=int, default=14)
    ap.add_argument("--restaurar", metavar="CARPETA")
    a = ap.parse_args(argv)
    if a.restaurar:
        print("Restaurado:", ", ".join(restaurar(a.restaurar)), "\nLo anterior quedó al lado con la terminación .antes_FECHA. Ahora podés prender el portal.")
        return 0
    if not a.destino:
        ap.error("falta la carpeta de destino")
    carpeta, hecho = hacer_respaldo(a.destino, a.n8n, a.con_pedidos, a.mantener)
    print(f"Respaldo listo en {carpeta}\n  - " + "\n  - ".join(hecho))
    return 0


if __name__ == "__main__":
    sys.exit(main())
