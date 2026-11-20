"""Mide cada etapa de la lectura de carpetas y avisa donde se va el tiempo.
Se corre con el portal frenado o abierto (no modifica nada):   python diagnostico.py
Usa la carpeta DOCS_ROOT del archivo .env."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import engine  # noqa: E402
from app.config import settings  # noqa: E402


def p(*a):
    print(*a, flush=True)


root = settings.docs_root
p("Carpeta:", root, "| existe:", root.exists())

t = time.monotonic()
idx, lnks = engine._walk(root)
p(f"1) Recorrido de carpetas: {time.monotonic() - t:.1f}s -> {len(idx.files)} planillas, {len(lnks)} accesos directos, {len(idx.dirs)} carpetas")

t = time.monotonic()
links, lentos = [], []
for i, (lp, k) in enumerate(lnks, 1):
    t1 = time.monotonic()
    links.append(engine._resolve_link(idx, lp, k, settings.link_map))
    dt = time.monotonic() - t1
    if dt > 1:
        lentos.append((dt, lp))
        p(f"   lento ({dt:.1f}s): {lp}")
    if i % 50 == 0:
        p(f"   ... {i}/{len(lnks)} accesos directos ({time.monotonic() - t:.1f}s)")
p(f"2) Lectura de accesos directos: {time.monotonic() - t:.1f}s ({len(lentos)} lentos)")
p("   servidores de red probados:", {h: ok for h, (_, ok) in engine._HOSTS.items()})

bad = [l for l in links if l["status"] == "ilegible" and l["kind"] == "lnk"]
p(f"3) Accesos directos que el lector propio no entendio: {len(bad)}")
if bad:
    t = time.monotonic()
    got = engine.linkmod.ps_read([l["lnk"] for l in bad])
    p(f"   lectura con Windows: {time.monotonic() - t:.1f}s ({len(got)} leidos)")

engine._load_cache()
pend = {}
for f in idx.files.values():
    hit = engine._CACHE.get(str(f))
    if not (hit and hit[0] == engine._sig(f)):
        pend[f.suffix.lower()] = pend.get(f.suffix.lower(), 0) + 1
p(f"4) Planillas que hay que leer de nuevo (no estan en el indice guardado): {pend or 'ninguna'}")

t = time.monotonic()
data, errors = engine.index_files(sorted(idx.files.values(), key=lambda x: str(x)))
p(f"5) Lectura del contenido: {time.monotonic() - t:.1f}s -> {len(data)} leidas, {len(errors)} con error")
for f, why in list(errors.items())[:5]:
    p("   error:", f.name, "->", why)
p("Listo.")
