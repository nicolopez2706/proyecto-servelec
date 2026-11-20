"""Inventario SOLO LECTURA de la carpeta de documentos. No modifica nada.
Sirve para conocer los datos reales antes de aplicar cambios: cuantos archivos hay, de que formato, cuales no se
pueden leer, cuales no tienen revision en el nombre, cuantas revisiones de mas hay y como estan los accesos directos.
Uso (dentro de backend, con el entorno virtual): python inventario.py   [ruta opcional, si no usa DOCS_ROOT del .env]
Deja un resumen en pantalla y el detalle en data\\inventario_problemas.csv"""
import csv
import sys
import time
from collections import Counter
from pathlib import Path

from app import engine
from app.config import settings


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else settings.docs_root
    if not root.exists():
        print(f"No existe la carpeta: {root}")
        return 1
    t0 = time.time()
    print(f"Carpeta: {root}\nLeyendo (la primera vez puede tardar, sobre todo con archivos .xls)…")
    d = engine.discover(root)
    files = sorted(d["files"], key=lambda p: str(p).lower())
    ext = Counter(p.suffix.lower() for p in files)
    locked = [p for p in files if engine.is_locked(p)]
    data, errors = engine.index_files([p for p in files if p not in locked])
    sin_rev = [p for p in files if not engine.file_info(p.name)]
    dups = engine.cleanup_folders(root, dry_run=True)
    codes = set()
    for cells in data.values():
        for _s, _c, t in cells:
            if " " not in t and len(t) <= 30 and any(ch.isdigit() for ch in t) and any(ch.isalpha() for ch in t):
                codes.add(t.upper())
    st = Counter(l["status"] for l in d["links"])
    print(f"\nArchivos de planilla: {len(files)}  " + "  ".join(f"{k}: {v}" for k, v in sorted(ext.items())))
    print(f"Leídos bien: {len(data)}   Abiertos por alguien: {len(locked)}   Que no se pudieron leer: {len(errors)}")
    print(f"Sin revisión en el nombre (L#V#R#): {len(sin_rev)}   Revisiones de más por limpiar: {len(dups)}")
    print(f"Códigos distintos encontrados (aprox.): {len(codes)}")
    print("Accesos directos: " + (", ".join(f"{k}: {v}" for k, v in sorted(st.items())) if st else "ninguno"))
    if ext.get(".xls") and not engine.find_soffice():
        print("\nAVISO: hay archivos .xls y no se encontró LibreOffice: esos no se pueden leer ni cambiar. Instalalo (libreoffice.org).")
    out = settings.data_dir / "inventario_problemas.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["tipo", "ruta", "detalle"])
        for p, why in errors.items():
            w.writerow(["no se pudo leer", p, why])
        for p in locked:
            w.writerow(["abierto por alguien", p, ""])
        for p in sin_rev:
            w.writerow(["sin revisión en el nombre", p, ""])
        for m in dups:
            w.writerow(["revisión de más", root / m["file"], "queda vigente " + m["keep"]])
        for l in d["links"]:
            if l["status"] != "ok":
                w.writerow([f"acceso directo: {l['status']}", l["lnk"], (l["raw"] or "") + " " + l["note"]])
    print(f"\nDetalle en: {out}\nTardó {time.time() - t0:.0f} s. No se modificó ningún archivo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
