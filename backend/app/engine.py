"""Motor de planillas: busca codigos, los cambia, renombra revisiones y arma la hoja
"Control de cambios". Formatos: .xlsx/.xlsm (openpyxl), .ods (XML, conserva formato) y
.xls (se convierte a .xlsx con LibreOffice; el archivo nuevo queda como .xlsx)."""
import datetime as dt
import json
import os
import re
import shutil
import socket
import subprocess
import functools
import tempfile
import threading
import time
import warnings
import zipfile
from pathlib import Path

from copy import copy

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from . import links as linkmod
from . import ods as odsmod
from .config import settings

XLSX_EXTS = {".xlsx", ".xlsm"}
EXTS = XLSX_EXTS | {".ods", ".xls"}
SKIP_DIRS = {"obsoleto", "_registros"}
OBS = "OBSOLETO"
CTL_SHEET = "Control de cambios"
SUG_HEADER = "Sugerencias"
FILE_RE = re.compile(r"(^|[_\-\s])([LD])(\d+)V(\d+)R(\d+)(?=$|[_\-\s.])", re.I)
DATE_RE = re.compile(r"(\d{2})-(\d{2})-(\d{4}|\d{2})")


def norm(code):
    return str(code or "").strip().upper()


# ---------------------------------------------------------------- nombres
def file_info(name):
    m = FILE_RE.search(name)
    if not m:
        return None
    pre, kind, n, v, r = m.groups()
    return {"kind": kind.upper(), "n": int(n), "v": int(v), "r": int(r), "tag": f"{kind}{n}V{v}R{r}",
            "family": name[: m.start()] + "|" + kind.upper() + n + "V" + v}


def bump_name(name, mode, today=None, new_ext=None):
    """mode 'rev': R+1. mode 'ver': V+1 y R0. Actualiza la fecha del nombre si la tiene.
    Si el nombre no tiene revision, lo deja igual (solo cambia la extension si hace falta)."""
    today = today or dt.date.today()
    m = FILE_RE.search(name)
    out = name
    if m:
        pre, kind, n, v, r = m.groups()
        v2, r2 = (int(v), int(r) + 1) if mode == "rev" else (int(v) + 1, 0)
        start = m.start() + len(pre)
        out = name[:start] + f"{kind}{n}V{v2}R{r2}" + name[m.end():]
        dm = DATE_RE.search(out, start)
        if dm:
            yr = today.strftime("%Y") if len(dm.group(3)) == 4 else today.strftime("%y")
            out = out[: dm.start()] + today.strftime("%d-%m-") + yr + out[dm.end():]
    if new_ext:
        out = str(Path(out).with_suffix(new_ext))
    return out


# ---------------------------------------------------------------- LibreOffice (.xls)
def find_soffice():
    cands = [os.getenv("SOFFICE_PATH"), shutil.which("soffice"), shutil.which("libreoffice"),
             r"C:\Program Files\LibreOffice\program\soffice.exe", r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"]
    for c in cands:
        if c and Path(c).exists():
            return c
    return None


def convert_to_xlsx(paths, outdir):
    """Convierte .xls a .xlsx en 'outdir' (una sola llamada a LibreOffice para todos)."""
    exe = find_soffice()
    if not exe:
        raise RuntimeError("falta LibreOffice para leer .xls (instalarlo o definir SOFFICE_PATH)")
    profile = Path(tempfile.mkdtemp(prefix="lo_profile_"))
    try:
        cmd = [exe, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--convert-to", "xlsx", "--outdir", str(outdir)]
        subprocess.run(cmd + [str(p) for p in paths], check=True, capture_output=True, timeout=600)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    return {Path(p): Path(outdir) / (Path(p).stem + ".xlsx") for p in paths}


# ---------------------------------------------------------------- documentos
class XlsxDoc:
    kind = "xlsx"

    def __init__(self, path, out_ext=None):
        self.path = Path(path)
        with warnings.catch_warnings(record=True) as avisos:
            warnings.simplefilter("always")
            self.wb = load_workbook(path, keep_vba=self.path.suffix.lower() == ".xlsm")
        # openpyxl avisa «... will be removed» cuando descarta algo que no sabe conservar (minigráficos, validaciones extendidas...)
        self.descartado = sorted({str(w.message).split(" extension")[0].split(" will be")[0][:80] for w in avisos
                                  if "removed" in str(w.message) or "lost" in str(w.message)})
        self.out_ext = out_ext or self.path.suffix

    @staticmethod
    def _qty_col(ws):
        for row in ws.iter_rows(min_row=1, max_row=15):
            for c in row:
                if isinstance(c.value, str) and c.value.strip().lower().startswith("cant"):
                    return c.column
        return None

    def apply_change(self, old, new, factor=None):
        old, new, changes = norm(old), norm(new), []
        for ws in self.wb.worksheets:
            if ws.title == CTL_SHEET:
                continue
            qcol = self._qty_col(ws) if factor not in (None, 1, 1.0) else None
            for row in ws.iter_rows():
                for c in row:
                    if c.value is not None and norm(c.value) == old:
                        c.value = new
                        item = {"sheet": ws.title, "cell": c.coordinate, "before": old, "after": new}
                        if qcol:
                            q = ws.cell(row=c.row, column=qcol)
                            if isinstance(q.value, (int, float)):
                                item["qty"] = {"before": q.value, "after": q.value * factor}
                                q.value = q.value * factor
                        changes.append(item)
        return changes

    @staticmethod
    def _header_row(ws):
        """Fila del encabezado de la tabla: la que dice 'Cantidad...' o, si no hay, la primera con contenido."""
        for row in ws.iter_rows(min_row=1, max_row=15):
            for c in row:
                if isinstance(c.value, str) and c.value.strip().lower().startswith("cant"):
                    return c.row
        for row in ws.iter_rows(min_row=1, max_row=15):
            if any(c.value not in (None, "") for c in row):
                return row[0].row
        return 1

    def add_suggestions(self, code, alts, header=SUG_HEADER):
        """Escribe los codigos sugeridos en una columna 'Sugerencias' (la crea si no existe) en cada fila donde
        esta 'code'. Si la fila ya tenia sugerencias, se suman sin repetir."""
        code, alts, changes = norm(code), [norm(a) for a in alts], []
        for ws in self.wb.worksheets:
            if ws.title == CTL_SHEET:
                continue
            hits = [c for row in ws.iter_rows() for c in row if c.value is not None and norm(c.value) == code]
            if not hits:
                continue
            hr = self._header_row(ws)
            col = next((c.column for c in ws[hr] if str(c.value or "").strip().lower().startswith("sugerencia")), None)
            if col is None:
                last = max(c.column for row in ws.iter_rows() for c in row if c.value not in (None, ""))
                col = last + 1
                h = ws.cell(row=hr, column=col, value=header)
                prev = ws.cell(row=hr, column=col - 1)
                if prev.has_style:
                    h.font, h.fill, h.border, h.alignment = copy(prev.font), copy(prev.fill), copy(prev.border), copy(prev.alignment)
                ws.column_dimensions[get_column_letter(col)].width = 28
            seen_rows = set()
            for c in hits:
                if c.row <= hr or c.row in seen_rows:
                    continue
                seen_rows.add(c.row)
                cell = ws.cell(row=c.row, column=col)
                cur = [x.strip() for x in str(cell.value or "").split(",") if x.strip()]
                have = {x.upper() for x in cur}
                merged = cur + [a for a in alts if a not in have]
                if merged != cur:
                    cell.value = ", ".join(merged)
                    changes.append({"sheet": ws.title, "cell": cell.coordinate, "before": ", ".join(cur), "after": cell.value})
        return changes

    def write_control(self, entry):
        if CTL_SHEET in self.wb.sheetnames:
            ws = self.wb[CTL_SHEET]
        else:
            ws = self.wb.create_sheet(CTL_SHEET)
            ws.append(["Revisión", "Qué cambió", "Motivo", "Quién lo pidió", "Quién lo autorizó", "Fecha", "Pedido"])
            for col, w in zip("ABCDEFG", (12, 60, 40, 24, 24, 12, 10)):
                ws.column_dimensions[col].width = w
        ws.append([entry["rev"], entry["what"], entry.get("why", ""), entry["by"], entry["auth"], entry["date"], entry["order"]])

    def save(self, dest):
        self.wb.save(dest)


# ---------------------------------------------------------------- que no se pierda contenido al guardar un .xlsx
# openpyxl vuelve a escribir el archivo entero y no conoce todo lo que Excel puede guardar: formas y cuadros de texto,
# tablas dinamicas, segmentaciones; y sin Pillow tampoco conserva imagenes. Antes de reemplazar nada se compara
# lo que tenia el original con lo que quedo en el archivo nuevo; si falta algo, ese archivo no se toca.
_PARTES = (("imágenes", r"^xl/media/"), ("gráficos", r"^xl/charts/chart\d*\.xml$"),
           ("tablas dinámicas", r"^xl/pivotTables/pivotTable\d*\.xml$"), ("segmentaciones", r"^xl/slicers/"),
           ("controles de formulario", r"^xl/ctrlProps/"), ("objetos incrustados", r"^xl/embeddings/"))
_OBJ_RE = re.compile(rb"<(?:\w+:)?(sp|pic|graphicFrame|cxnSp|grpSp)[ >/]")


def _contenido_xlsx(path):
    """{tipo de contenido: cantidad} de lo que openpyxl podria perder."""
    out = {}
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        for etiqueta, pat in _PARTES:
            n = sum(1 for x in names if re.search(pat, x))
            if n:
                out[etiqueta] = n
        formas = 0
        for x in names:
            if re.match(r"^xl/drawings/drawing\d*\.xml$", x):
                formas += len(_OBJ_RE.findall(z.read(x)))
        if formas:
            out["dibujos y formas"] = formas
    return out


def check_no_loss(original, nuevo):
    """Falla si el archivo guardado perdio algo de lo que tenia el original."""
    if settings.allow_content_loss:
        return
    antes, despues = _contenido_xlsx(original), _contenido_xlsx(nuevo)
    perdido = [f"{k} ({v} → {despues.get(k, 0)})" for k, v in antes.items() if despues.get(k, 0) < v]
    if perdido:
        raise ValueError("no se modificó porque se perdería contenido que el portal no sabe conservar: " + ", ".join(perdido)
                         + ". Cambialo a mano en esa planilla.")


def open_doc(path):
    ext = Path(path).suffix.lower()
    if ext in XLSX_EXTS:
        return XlsxDoc(path)
    if ext == ".ods":
        return odsmod.OdsDoc(path)
    if ext == ".xls":
        tmp = Path(tempfile.mkdtemp(prefix="xls_"))
        try:
            conv = convert_to_xlsx([path], tmp)[Path(path)]
            doc = XlsxDoc(conv, out_ext=".xlsx")
            doc._tmpdir = tmp
            return doc
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    raise ValueError(f"formato {ext} no soportado")


# ---------------------------------------------------------------- indice (con cache)
_CACHE = {}          # ruta -> (firma, [(hoja, celda, texto)])
_CACHE_LOADED = False
_DIRTY = False       # hay celdas nuevas en memoria que todavia no se guardaron en disco
_BLOB = {}           # ruta -> (firma, texto en mayusculas de todo el archivo, celdas): filtro rapido para buscar
_SNAP = {}           # raiz -> (momento, datos, omitidos): resultado reciente de scan() para consultas de lectura
_IDX_LOCK = threading.RLock()
_SNAP_LOCK = threading.RLock()


def invalidate_scan():
    """Olvida el resultado reciente de scan(): se llama en todo lo que modifica archivos."""
    _SNAP.clear()


def _writes(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        invalidate_scan()
        try:
            return fn(*a, **k)
        finally:
            invalidate_scan()
    return wrapper


def _cache_file():
    return settings.data_dir / "indice_celdas.json"


def _load_cache():
    global _CACHE_LOADED
    if _CACHE_LOADED:
        return
    _CACHE_LOADED = True
    try:
        raw = json.loads(_cache_file().read_text(encoding="utf-8"))
        _CACHE.update({k: (tuple(v[0]), [tuple(c) for c in v[1]]) for k, v in raw.items()})
    except Exception:
        pass


def save_cache():
    """Guarda el indice en disco solo si cambio algo (antes se reescribia entero en cada busqueda)."""
    global _DIRTY
    if not _DIRTY:
        return
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = _cache_file().with_suffix(".json.tmp")
        tmp.write_text(json.dumps({k: [list(v[0]), [list(c) for c in v[1]]] for k, v in _CACHE.items()}), encoding="utf-8")
        os.replace(tmp, _cache_file())
        _DIRTY = False
    except Exception:
        pass


def reset_cache():
    global _CACHE_LOADED, _DIRTY
    _CACHE.clear()
    _BLOB.clear()
    _SNAP.clear()
    _CACHE_LOADED = False
    _DIRTY = False


def _blob(p, cells):
    """Todo el texto del archivo en mayusculas, en una sola cadena: permite descartar de un vistazo los archivos
    que no contienen lo buscado sin recorrer sus celdas una por una."""
    key = str(p)
    ent = _CACHE.get(key)
    b = _BLOB.get(key)
    if ent is not None and b is not None and b[0] == ent[0] and b[2] is cells:
        return b[1]
    text = "\x00".join(t.upper() for _s, _c, t in cells)
    if ent is not None:
        _BLOB[key] = (ent[0], text, cells)
    return text


def _sig(p):
    st = p.stat()
    return (st.st_mtime_ns, st.st_size)


def _read_xlsx_cells(path):
    wb = load_workbook(path, read_only=True, data_only=False)
    out = []
    for ws in wb.worksheets:
        if ws.title == CTL_SHEET:
            continue
        for row in ws.iter_rows():
            for c in row:
                if c.value is not None and str(c.value).strip():
                    out.append((ws.title, c.coordinate, str(c.value).strip()))
    wb.close()
    return out


def index_files(paths):
    with _IDX_LOCK:
        return _index_files(paths)


def _index_files(paths):
    """Devuelve {ruta: celdas}. Lee solo lo que cambio desde la ultima vez. Los .xls pendientes
    se convierten todos juntos con una sola llamada a LibreOffice."""
    global _DIRTY
    _load_cache()
    res, errors, pend_xls = {}, {}, []
    for p in paths:
        sg = _sig(p)
        hit = _CACHE.get(str(p))
        if hit and hit[0] == sg:
            res[p] = hit[1]
            continue
        if p.suffix.lower() == ".xls":
            pend_xls.append(p)
            continue
        try:
            cells = _read_xlsx_cells(p) if p.suffix.lower() in XLSX_EXTS else odsmod.OdsDoc(p).cells()
            _CACHE[str(p)] = (sg, cells); res[p] = cells; _DIRTY = True
        except Exception as e:
            errors[p] = f"no se pudo abrir ({type(e).__name__})"
    if pend_xls:
        tmp = Path(tempfile.mkdtemp(prefix="xlsidx_"))
        try:
            conv = convert_to_xlsx(pend_xls, tmp)
            for p, c in conv.items():
                try:
                    cells = _read_xlsx_cells(c)
                    _CACHE[str(p)] = (_sig(p), cells); res[p] = cells; _DIRTY = True
                except Exception as e:
                    errors[p] = f"no se pudo convertir ({type(e).__name__})"
        except Exception as e:
            for p in pend_xls:
                errors[p] = str(e) if isinstance(e, RuntimeError) else f"no se pudo convertir ({type(e).__name__})"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    save_cache()
    return res, errors


# ---------------------------------------------------------------- servidores de red que no responden
# Los accesos directos guardan el destino como letra de unidad (Z:\\...) y como \\\\servidor\\recurso. Desde una computadora
# que no esta en la red de la oficina, preguntarle a Windows si ese archivo existe puede quedar esperando minutos
# (por cada acceso directo). Se evita de dos maneras: se prueba una vez si el servidor responde, y cualquier consulta
# a la red tiene un tiempo maximo de espera; si se pasa, ese servidor/unidad queda marcado como caido por un rato.
NET_TIMEOUT = 5.0
_HOSTS = {}
_DRV = {}


def _drive_host(letter):
    """None = unidad local; '' = unidad de red de la que no se sabe el servidor; 'host' = unidad de red conectada a ese servidor."""
    if os.name != "nt":
        return None
    hit = _DRV.get(letter)
    if hit and time.monotonic() - hit[0] < 120:
        return hit[1]
    res = None
    try:
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(letter + ":\\") == 4:            # 4 = unidad de red
            buf, n = ctypes.create_unicode_buffer(1024), ctypes.c_ulong(1024)
            r = ctypes.windll.mpr.WNetGetConnectionW(letter + ":", buf, ctypes.byref(n))   # 0 conectada, 1201 recordada pero desconectada
            res = buf.value[2:].split("\\", 1)[0] if r in (0, 1201) and buf.value.startswith("\\\\") else ""
    except Exception:
        res = None
    _DRV[letter] = (time.monotonic(), res)
    return res


def _net_target(path):
    """(clave, servidor) si la ruta esta en la red; (None, None) si es local."""
    s = str(path).replace("/", "\\")
    if s.startswith("\\\\"):
        host = s[2:].split("\\", 1)[0]
        return (host.lower(), host) if host else (None, None)
    if len(s) >= 2 and s[1] == ":" and s[0].isalpha():
        h = _drive_host(s[0].upper())
        return (None, None) if h is None else (s[0].upper() + ":", h)
    return None, None


def _reachable(path):
    key, host = _net_target(path)
    if key is None:
        return True
    now = time.monotonic()
    hit = _HOSTS.get(key)
    if hit and now - hit[0] < 120:
        return hit[1]
    ok = True
    if host:
        try:
            with socket.create_connection((host, 445), timeout=1.5):
                pass
        except OSError:
            ok = False
    if hit is None or hit[1] != ok or now - hit[0] >= 120:
        _HOSTS[key] = (now, ok)
    return ok


def _net_call(fn, c):
    """Consulta al sistema de archivos con tiempo maximo si la ruta esta en la red."""
    key, _ = _net_target(c)
    if key is None:
        return fn(c)
    if not _reachable(c):
        return False
    out = []
    t = threading.Thread(target=lambda: out.append(fn(c)), daemon=True, name="consulta-red")
    t.start()
    t.join(NET_TIMEOUT)
    if t.is_alive():                                    # Windows sigue esperando: se da por caido ese servidor/unidad
        _HOSTS[key] = (time.monotonic(), False)
        return False
    return bool(out and out[0])


def _exists(c):
    return _net_call(os.path.exists, c)


def _isdir(c):
    return _net_call(os.path.isdir, c)


# ---------------------------------------------------------------- rutas
LINK_EXTS = {".lnk", ".url", ".gsheet", ".gdoc"}
GOOGLE_EXTS = (".gsheet", ".gdoc", ".gslides")
OK_STATES = ("ok", "desactualizado")      # estados en los que el acceso directo se puede mantener al revisar


def _skip_dir(name):
    n = name.lower()
    return n in SKIP_DIRS or n in settings.exclude_dirs or n in (".git", "node_modules", "__pycache__", "$recycle.bin")


def _nk(p):
    """Clave para comparar rutas sin importar mayusculas ni el tipo de barra."""
    return str(p).replace("\\", "/").rstrip("/").lower()


def _native(s):
    return s if os.name == "nt" else s.replace("\\", "/")


def roots_list(root):
    """[(etiqueta, carpeta)]: la raiz de documentos y las carpetas extra (EXTRA_ROOTS)."""
    return [("", Path(root))] + [(n, Path(p)) for n, p in settings.extra_roots]


def to_rel(root, path):
    path = Path(path)
    for label, base in roots_list(root):
        try:
            r = str(path.relative_to(base)).replace("\\", "/")
        except ValueError:
            continue
        if r == ".":
            return "." if not label else f"@{label}"
        return r if not label else f"@{label}/{r}"
    raise ValueError(f"{path} está fuera de las carpetas configuradas")


def from_rel(root, rel):
    if rel.startswith("@"):
        label, _, r = rel[1:].partition("/")
        for n, base in roots_list(root)[1:]:
            if n == label:
                return base / r
        raise ValueError(f"carpeta extra desconocida: {label}")
    return Path(root) / rel


def rel_folder(root, path):
    return to_rel(root, Path(path).parent)


def is_locked(path):
    if settings.ignore_locks:
        return False
    p = Path(path)
    return (p.parent / ("~$" + p.name)).exists() or (p.parent / (".~lock." + p.name + "#")).exists()


# ---------------------------------------------------------------- recorrido y accesos directos
class _Index:
    def __init__(self):
        self.files, self.dirs = {}, {}           # nk -> Path (solo lo recorrido)
        self.by_dir, self.names, self.dnames = {}, {}, {}
        self.bases = []

    def add_dir(self, p):
        self.dirs[_nk(p)] = p
        self.dnames.setdefault(p.name.lower(), []).append(p)

    def add_file(self, p):
        self.files[_nk(p)] = p
        self.by_dir.setdefault(_nk(p.parent), []).append(p)
        self.names.setdefault(p.name.lower(), []).append(p)


def _walk(root):
    """Recorre la raiz y las carpetas extra. Devuelve (indice, archivos, accesos directos)."""
    idx, lnks = _Index(), []
    seen = set()
    for label, base in roots_list(root):
        if not base.exists():
            continue
        idx.bases.append(base)
        for dirpath, dirnames, filenames in os.walk(base):
            dp = Path(dirpath)
            keep = []
            for d in sorted(dirnames):
                if _skip_dir(d):
                    continue
                full = dp / d
                if os.path.islink(full):
                    lnks.append((full, "symlink"))
                else:
                    keep.append(d)
            dirnames[:] = keep
            idx.add_dir(dp)
            for fn in sorted(filenames):
                if fn.startswith(("~$", ".tmp_", ".~lock")):
                    continue
                full = dp / fn
                ext = full.suffix.lower()
                if ext in LINK_EXTS:
                    lnks.append((full, ext[1:]))
                elif os.path.islink(full):
                    lnks.append((full, "symlink"))
                elif ext in EXTS and _nk(full) not in seen:
                    seen.add(_nk(full))
                    idx.add_file(full)
    return idx, lnks


def _unique(lst):
    return lst[0] if len(lst) == 1 else None


def _find_dir(idx, raws, lnk_path, rel, maps):
    """Busca una carpeta por: ruta directa, mapeo, ruta relativa, cola de la ruta, nombre unico.
    Devuelve (Path|None, metodo, raw_usado, raiz_perdida, n_candidatos)."""
    cands = []
    for raw in raws:
        cands.append((_native(raw), "directo", raw))
        for src, dst in maps:
            if _nk(raw) == _nk(src) or _nk(raw).startswith(_nk(src) + "/"):
                cands.append((_native(dst + raw[len(src):]), "mapeado", raw))
    if rel:
        cands.append((str(Path(lnk_path).parent / _native(rel)), "relativo", raws[0] if raws else rel))
    for c, how, raw in cands:
        if _isdir(c):
            return idx.dirs.get(_nk(Path(c)), Path(c)), how, raw, "", 1
    for raw in raws[:1]:
        parts = [x for x in raw.replace("/", "\\").split("\\") if x and not x.endswith(":")]
        for k in range(len(parts), 1, -1):
            tail = parts[-k:]
            for base in idx.bases:
                c = base.joinpath(*tail)
                if os.path.isdir(c):
                    lost = raw[: len(raw.rstrip("\\")) - len("\\".join(tail))].rstrip("\\")
                    return idx.dirs.get(_nk(c), c), "sufijo", raw, lost, 1
        if parts:
            c = idx.dnames.get(parts[-1].lower(), [])
            if len(c) == 1:
                return c[0], "nombre", raw, "", 1
            if len(c) > 1:
                return None, "ambiguo", raw, "", len(c)
    return None, "", "", "", 0


def _family_newest(idx, folder, name):
    """Archivo vigente de la misma familia (misma version, mayor revision) en esa carpeta."""
    info = file_info(name)
    if not info:
        return None
    best = None
    for f in idx.by_dir.get(_nk(folder), []):
        fi = file_info(f.name)
        if fi and fi["family"] == info["family"]:
            key = (fi["r"], f.suffix.lower() != ".xls")
            if best is None or key > best[0]:
                best = (key, f)
    return best[1] if best else None


def _resolve_link(idx, lnk_path, kind, maps, info_override=None):
    out = {"lnk": Path(lnk_path), "kind": kind, "is_dir": False, "raw": None, "status": "roto", "method": "",
           "target": None, "lost": "", "note": "", "drive_name": "", "drive_id": ""}
    try:
        if info_override is not None:
            info = info_override
        elif kind == "symlink":
            real = os.path.realpath(lnk_path)
            info = {"raws": [real], "rel": "", "is_dir": os.path.isdir(real)}
        else:
            info = linkmod.read_link(lnk_path)
    except Exception as e:
        out.update(status="ilegible", note=f"no se pudo leer ({type(e).__name__})")
        return out
    if info.get("url") and not info["raws"]:
        sid = re.search(r"/d/([A-Za-z0-9_-]+)", info["url"])
        out.update(status="drive" if "google.com" in info["url"] else "ilegible", raw=info["url"],
                   note=("Google Sheets (id " + sid.group(1)[:12] + "…): todavía no se modifica desde el portal") if sid and "google.com" in info["url"]
                   else "es un enlace web, no un archivo")
        return out
    raws, rel, is_dir = info["raws"], info["rel"], info["is_dir"]
    gtarget = next((r for r in raws if r.lower().endswith(GOOGLE_EXTS)), None)
    if gtarget:                                       # acceso directo a un Google Sheets dentro de Google Drive para escritorio
        out.update(status="drive", raw=gtarget, method="google")
        out["drive_name"] = gtarget.replace("/", "\\").rsplit("\\", 1)[-1]
        try:                                          # si G: esta disponible, el .gsheet trae el id del documento
            j = json.loads(Path(_native(gtarget)).read_text(encoding="utf-8-sig"))
            out["drive_id"] = j.get("doc_id") or ""
        except Exception:
            out["drive_id"] = ""
        out["note"] = ("Google Sheets «" + out["drive_name"] + "»" + (" (id leído)" if out["drive_id"] else " (G: no disponible en esta computadora)")
                       + ": todavía no se modifica desde el portal")
        return out
    out["is_dir"] = is_dir
    if not raws and not rel:
        out.update(status="ilegible", note="no tiene destino que se pueda leer")
        return out
    out["raw"] = raws[0] if raws else rel
    if is_dir:
        d, how, raw, lost, n = _find_dir(idx, raws, lnk_path, rel, maps)
        if d is not None:
            inside = _nk(d) in idx.dirs
            out.update(target=d, method=how, raw=raw, lost=lost,
                       status=("ok" if how in ("directo", "mapeado", "relativo") else "aprox") if inside else "fuera")
        elif how == "ambiguo":
            out.update(status="ambiguo", note=f"hay {n} carpetas con ese nombre")
        else:
            out.update(note="la carpeta no existe")
        return out
    # --- archivo
    cands = []
    for raw in raws:
        cands.append((_native(raw), "directo", raw))
        for src, dst in maps:
            if _nk(raw) == _nk(src) or _nk(raw).startswith(_nk(src) + "/"):
                cands.append((_native(dst + raw[len(src):]), "mapeado", raw))
    if rel:
        cands.append((str(Path(lnk_path).parent / _native(rel)), "relativo", raws[0] if raws else rel))
    hit = None
    for c, how, raw in cands:
        if _exists(c):
            hit = (Path(c), how, raw, "")
            break
    name = (raws[0] if raws else rel).replace("/", "\\").rsplit("\\", 1)[-1]
    if hit is None and raws:
        parts = [x for x in raws[0].replace("/", "\\").split("\\") if x and not x.endswith(":")]
        for k in range(len(parts), 1, -1):
            tail = parts[-k:]
            found = None
            for base in idx.bases:
                if os.path.isfile(base.joinpath(*tail)):
                    found = base.joinpath(*tail)
                    break
            if found:
                hit = (found, "sufijo", raws[0], raws[0][: len(raws[0].rstrip("\\")) - len("\\".join(tail))].rstrip("\\"))
                break
    if hit is not None:
        path, how, raw, lost = hit
        canon = idx.files.get(_nk(path))
        out.update(method=how, raw=raw, lost=lost)
        if canon is not None:
            newest = _family_newest(idx, canon.parent, canon.name)
            if newest is not None and _nk(newest) != _nk(canon):
                out.update(status="desactualizado", target=newest, note=f"apunta a {canon.name}; la vigente es {newest.name}")
            else:
                out.update(status="ok" if how in ("directo", "mapeado", "relativo") else "aprox", target=canon)
            return out
        par = path.parent
        if par.name.upper() == OBS and _nk(par.parent) in idx.dirs:
            newest = _family_newest(idx, par.parent, path.name)
            if newest is not None:
                out.update(status="desactualizado", target=newest, note=f"apunta a OBSOLETO; la vigente es {newest.name}")
                return out
        out.update(status="fuera", target=path, note="existe pero está fuera de las carpetas configuradas")
        return out
    # no existe: buscar la carpeta y la revision vigente, o el nombre en todo el arbol
    d, how, raw, lost, n = _find_dir(idx, [raws[0].rsplit("\\", 1)[0]] if raws else [], lnk_path, "", maps)
    if d is not None:
        d_eff = d.parent if d.name.upper() == OBS else d
        exact = idx.files.get(_nk(d_eff / name))
        newest = exact and _family_newest(idx, d_eff, exact.name) or _family_newest(idx, d_eff, name)
        tgt = newest or exact
        if tgt is not None:
            same = exact is not None and _nk(tgt) == _nk(exact)
            out.update(method=how, lost=lost, target=tgt,
                       status=("ok" if how in ("directo", "mapeado") else "aprox") if same else "desactualizado",
                       note="" if same else f"apunta a {name}, que ya no está; la vigente es {tgt.name}")
            return out
    same = idx.names.get(name.lower(), [])
    if len(same) == 1:
        out.update(status="aprox", method="nombre", target=same[0], note="encontrado solo por el nombre del archivo")
    elif len(same) > 1:
        out.update(status="ambiguo", note=f"hay {len(same)} archivos con ese nombre")
    else:
        out.update(note="el archivo no existe")
    return out


def discover(root):
    """Recorre todo: archivos de planilla (en la raiz y en carpetas extra) y accesos directos.
    Devuelve {"files": [Path], "links": [dict], "via": {clave_archivo: [dict]}, "index": _Index}."""
    t0 = time.monotonic()
    idx, lnk_files = _walk(root)
    t1 = time.monotonic()
    maps = settings.link_map
    links = [_resolve_link(idx, p, k, maps) for p, k in lnk_files]
    t2 = time.monotonic()
    bad = [l for l in links if l["status"] == "ilegible" and l["kind"] == "lnk"]
    if bad and os.name == "nt":                     # segundo intento con Windows
        got = linkmod.ps_read([l["lnk"] for l in bad])
        for l in bad:
            t = got.get(str(l["lnk"]))
            if t:
                l.update(_resolve_link_from_target(idx, l["lnk"], t, maps))
    via = {}
    for l in links:
        if l["status"] in OK_STATES + ("aprox",) and l["target"] is not None and not l["is_dir"]:
            via.setdefault(_nk(l["target"]), []).append(l)
    drive_dir = {}
    for l in links:
        if l["status"] == "drive":
            drive_dir.setdefault(_nk(Path(l["lnk"]).parent), []).append(l)
    t3 = time.monotonic()
    if t3 - t0 > 2:                                 # aviso en la consola del portal: ayuda a ver donde se va el tiempo
        print(f"[portal] recorrido de carpetas: {t3 - t0:.1f}s en total (carpetas {t1 - t0:.1f}s, accesos directos {t2 - t1:.1f}s, "
              f"lectura con Windows de {len(bad)} ilegibles {t3 - t2:.1f}s; {len(idx.files)} planillas, {len(links)} accesos)", flush=True)
    return {"files": list(idx.files.values()), "links": links, "via": via, "index": idx, "drive_dir": drive_dir}


def _resolve_link_from_target(idx, lnk_path, target, maps):
    """Reusa la resolucion cuando Windows ya nos dio el destino en texto."""
    info = {"raws": [target], "rel": "", "is_dir": _isdir(_native(target))}
    return _resolve_link(idx, lnk_path, "lnk", maps, info_override=info)


def _via_entries(root, d, p):
    out = []
    for l in d["via"].get(_nk(p), []):
        out.append({"lnk": to_rel(root, l["lnk"]), "kind": l["kind"], "status": l["status"], "raw": l["raw"]})
    return out


def _drive_twin(root, d, p):
    """Planilla gemela en Google Sheets: acceso directo a un .gsheet en la misma carpeta del archivo o en la de un
    nivel o dos mas arriba (carpeta del producto)."""
    cur = Path(p).parent
    for _ in range(3):
        hit = d["drive_dir"].get(_nk(cur))
        if hit:
            return [{"lnk": to_rel(root, l["lnk"]), "name": l["drive_name"], "id": l["drive_id"]} for l in hit]
        if _nk(cur) in {_nk(b) for b in d["index"].bases}:
            break
        cur = cur.parent
    return []


def scan_full(root):
    """(datos, omitidos, descubrimiento)."""
    d = discover(root)
    ok, skipped = [], []
    for p in sorted(d["files"], key=lambda x: _nk(x)):
        if is_locked(p):
            skipped.append({"file": to_rel(root, p), "why": "abierto por otra persona"})
        else:
            ok.append(p)
    data, errors = index_files(ok)
    for p, why in errors.items():
        skipped.append({"file": to_rel(root, p), "why": why})
    return {p: data[p] for p in ok if p in data}, skipped, d


def scan(root, ttl=0):
    """Todos los archivos soportados con su contenido: (archivos, omitidos).
    Con ttl > 0 (segundos) reusa el resultado de hace poco: sirve para consultas de lectura (buscar, catalogo)
    y evita volver a recorrer el servidor en cada pedido. Todo lo que modifica archivos lo descarta.
    Quien llame con ttl no debe modificar lo devuelto."""
    key = str(root)
    if ttl:
        hit = _SNAP.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1], hit[2]
    with _SNAP_LOCK:
        if ttl:                                         # otro hilo pudo haberlo calculado mientras esperabamos
            hit = _SNAP.get(key)
            if hit and time.monotonic() - hit[0] < ttl:
                return hit[1], hit[2]
        data, skipped, _ = scan_full(root)
        if ttl:
            _SNAP[key] = (time.monotonic(), data, skipped)
    return data, skipped


def simulate(root, code):
    """Que pasaria con 'code': carpetas -> archivos -> celdas. No modifica nada.
    Cada archivo trae 'via': los accesos directos que apuntan a el."""
    root, code = Path(root), norm(code)
    data, skipped, d = scan_full(root)
    folders = {}
    for p, cells in data.items():
        if code not in _blob(p, cells):
            continue
        hits = [[s, c] for s, c, t in cells if t.upper() == code]
        if hits:
            e = {"file": to_rel(root, p), "cells": hits}
            v = _via_entries(root, d, p)
            if v:
                e["via"] = v
            g = _drive_twin(root, d, p)
            if g:
                e["drive"] = g
            folders.setdefault(rel_folder(root, p), []).append(e)
    folders = {k: sorted(v, key=lambda x: x["file"]) for k, v in sorted(folders.items())}
    return {"code": code, "folders": folders,
            "total_cells": sum(len(f["cells"]) for fs in folders.values() for f in fs),
            "total_files": sum(len(fs) for fs in folders.values()), "skipped": skipped,
            "links_total": sum(len(f.get("via", [])) for fs in folders.values() for f in fs),
            "drive_files": sum(1 for fs in folders.values() for f in fs if f.get("drive"))}


def search_text(root, text, limit=200):
    """Busca texto dentro de cualquier celda (codigos o descripciones)."""
    return search_full(root, text, limit)["items"]


def explain_skipped(skipped, soffice=None):
    """Texto claro de por que hay archivos que no se pudieron leer (para avisar en vez de callar)."""
    if not skipped:
        return ""
    xls = [x for x in skipped if x["file"].lower().endswith(".xls")]
    por = {}
    for x in skipped:
        por[x["why"]] = por.get(x["why"], 0) + 1
    partes = [f"{n} ({w})" for w, n in sorted(por.items(), key=lambda kv: -kv[1])]
    t = f"{len(skipped)} planillas NO se pudieron leer y podrían contener el código: " + "; ".join(partes) + "."
    if xls and not (soffice if soffice is not None else find_soffice()):
        t += " Hay archivos .xls y falta instalar LibreOffice en esta computadora (libreoffice.org): sin él no se pueden leer ni cambiar."
    return t


def search_full(root, text, limit=200):
    root, q = Path(root), text.strip().upper()
    data, skipped = scan(root, settings.scan_ttl)
    out = []
    for p, cells in data.items():
        if q not in _blob(p, cells):
            continue
        hits = [(s, c, t) for s, c, t in cells if q in t.upper()]
        if hits:
            out.append({"file": to_rel(root, p), "folder": rel_folder(root, p),
                        "hits": [{"sheet": s, "cell": c, "text": t[:120]} for s, c, t in hits[:5]], "count": len(hits)})
        if len(out) >= limit:
            break
    return {"items": out, "skipped": skipped, "skipped_note": explain_skipped(skipped)}


def _split_coord(coord):
    m = re.fullmatch(r"([A-Z]+)(\d+)", coord)
    n = 0
    for ch in m.group(1):
        n = n * 26 + ord(ch) - 64
    return n, int(m.group(2))


def _col_letter(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


_HEAD_CODE = re.compile(r"(?i)^\s*(c[oó]d(igo|\.)?(\s+(magnus|art[ií]culo|producto|material))?|codigoarticulo|c[oó]digo\s+\w+)\s*$")
_HEAD_DESC = re.compile(r"(?i)^\s*(detalle|descripci[oó]n|denominaci[oó]n)\s*$")
_HEAD_REF = re.compile(r"(?i)^\s*(referencias?|ref\.?|posici[oó]n|designaci[oó]n)\s*$")
# lo que parece codigo pero no lo es cuando no esta en una columna de codigos: etiquetas de version (L1V1R0, PDS_V2R1)
# y referencias de circuito (K1, X2, TR1, SW1)
_NOT_CODE = re.compile(r"^(?:[LD]\d+V\d+R\d+|[A-Z_]*V\d+R\d+|[A-Z]{1,9}\d{1,2})$", re.I)
_CODE_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-_]*")
_CODE_COL_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-_/]*")


def _code_columns(cells):
    """({(hoja, columna): fila_del_encabezado} de codigos, {(hoja, columna)} de referencias, {hoja: columna} de descripcion).
    Los encabezados se buscan en las primeras filas."""
    cod, ref, desc = {}, set(), {}
    for s_, c_, t in cells:
        if len(t) > 40:
            continue
        m = re.fullmatch(r"([A-Z]+)(\d+)", c_)
        if not m or int(m.group(2)) > 25:
            continue
        if _HEAD_CODE.match(t):
            cod.setdefault((s_, m.group(1)), int(m.group(2)))
        elif _HEAD_REF.match(t):
            ref.add((s_, m.group(1)))
        elif _HEAD_DESC.match(t):
            desc.setdefault(s_, m.group(1))
    return cod, ref, desc


def code_usage(root):
    """{codigo: {cells, folders, desc}} para las celdas que parecen codigos. La descripcion es el texto
    de la celda de la derecha (o de la siguiente con texto no numerico) en la misma fila.
    Una celda debajo de un encabezado de codigo («Código», «Código Magnus»…) es un codigo. Fuera de esas columnas
    solo cuenta si tiene forma de codigo y no es una etiqueta de version ni una referencia de circuito (K1, X2, L1V1R0)."""
    root = Path(root)
    data, _ = scan(root, settings.scan_ttl)
    use = {}
    for p, cells in data.items():
        fol = rel_folder(root, p)
        grid = {(s, c): t for s, c, t in cells}
        cod, ref, dcol = _code_columns(cells)
        for s_, c_, t in cells:
            col_l = re.match(r"[A-Z]+", c_).group(0)
            row_n = int(c_[len(col_l):])
            if (s_, col_l) in ref:
                continue
            en_col = (s_, col_l) in cod and row_n > cod[(s_, col_l)]
            if en_col:
                if " " in t or len(t) > 30 or not _CODE_COL_SHAPE.fullmatch(t) or t.replace(".", "").replace(",", "").isdigit():
                    continue
            elif " " in t or len(t) > 30 or not re.search(r"\d", t) or not re.search(r"[A-Za-z]", t) \
                    or not _CODE_SHAPE.fullmatch(t) or _NOT_CODE.fullmatch(t):
                continue
            e = use.setdefault(t.upper(), {"cells": 0, "folders": set(), "desc": ""})
            e["cells"] += 1; e["folders"].add(fol)
            if not e["desc"] and en_col and s_ in dcol:         # la columna «Detalle»/«Descripción» de esa hoja
                d_ = grid.get((s_, f"{dcol[s_]}{row_n}"))
                if d_ and re.search(r"[A-Za-z]{3}", d_):
                    e["desc"] = d_[:120]
            if not e["desc"]:
                col, row = _split_coord(c_)
                for k in (1, 2):
                    nxt = grid.get((s_, f"{_col_letter(col + k)}{row}"))
                    if nxt and re.search(r"[A-Za-z]{3}", nxt) and not nxt.replace(".", "").isdigit():
                        e["desc"] = nxt[:120]; break
    return {k: {"cells": v["cells"], "folders": len(v["folders"]), "desc": v["desc"]} for k, v in use.items()}


# ---------------------------------------------------------------- cambios
@_writes
def revise_and_save(path, doc, mode, entry, today=None):
    """Guarda el archivo modificado con el nombre nuevo.
    rev: el original va a OBSOLETO (pisa si ya hay uno igual). ver: el original sigue vigente."""
    path = Path(path)
    folder = path.parent
    ext_changed = doc.out_ext.lower() != path.suffix.lower()
    newname = bump_name(path.name, mode, today, new_ext=doc.out_ext if ext_changed else None)
    newpath = folder / newname
    if mode == "ver" and newname == path.name:
        raise ValueError("el nombre no tiene versión (L#V#R#): no se puede crear una versión nueva")
    if newpath.exists() and newpath != path:
        raise ValueError(f"ya existe {newname}")
    info = file_info(newname)
    doc.write_control(dict(entry, rev=info["tag"] if info else "—"))
    tmp = folder / (".tmp_" + newname)
    doc.save(tmp)
    if getattr(doc, "kind", "") == "xlsx" and path.suffix.lower() in XLSX_EXTS:
        try:
            check_no_loss(path, tmp)
            if getattr(doc, "descartado", None) and not settings.allow_content_loss:
                raise ValueError("no se modificó porque se perdería contenido que el portal no sabe conservar: "
                                 + ", ".join(doc.descartado) + ". Cambialo a mano en esa planilla.")
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
    obs_path, obs_prev, moved = None, False, False
    try:
        if mode == "rev":
            (folder / OBS).mkdir(exist_ok=True)
            obs_path = folder / OBS / path.name
            obs_prev = obs_path.exists()
            os.replace(path, obs_path)
            moved = True
        os.replace(tmp, newpath)
    except Exception:
        if moved and not path.exists():       # deshacer el movimiento a OBSOLETO si fallo el segundo paso
            try:
                os.replace(obs_path, path)
            except Exception:
                pass
        if tmp.exists():
            tmp.unlink()
        raise
    if getattr(doc, "_tmpdir", None):
        shutil.rmtree(doc._tmpdir, ignore_errors=True)
    return {"orig": str(path), "new": str(newpath), "obsoleto": str(obs_path) if obs_path else None, "obs_overwrote": obs_prev}


def backup_file(root, path, backup_dir):
    rel = to_rel(root, path).replace("@", "_extra_", 1)
    dest = Path(backup_dir) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    return str(dest)


@_writes
def restore(item, backup_orig, backup_obs_prev=None):
    """Deshace un archivo: borra el nuevo y vuelve el original desde el respaldo."""
    orig, new = Path(item["orig"]), Path(item["new"])
    if new.exists() and new != orig:
        new.unlink()
    shutil.copy2(backup_orig, orig)
    if item.get("obsoleto"):
        obs = Path(item["obsoleto"])
        if backup_obs_prev and Path(backup_obs_prev).exists():
            shutil.copy2(backup_obs_prev, obs)
        elif obs.exists():
            obs.unlink()
        if obs.parent.exists() and not any(obs.parent.iterdir()):
            obs.parent.rmdir()


@_writes
def cleanup_folders(root, dry_run=False):
    """Nunca conviven dos revisiones de la misma version: deja la mayor y manda el resto a OBSOLETO
    (pisando duplicados). Recorre todo el arbol (y las carpetas extra)."""
    moved = []
    for label, base in roots_list(root):
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not _skip_dir(d)]
            fams = {}
            for fn in filenames:
                if fn.startswith(("~$", ".tmp_", ".~lock")):
                    continue
                info = file_info(fn)
                if info and Path(fn).suffix.lower() in EXTS:
                    fams.setdefault(info["family"], []).append((info["r"], Path(fn).suffix.lower() != ".xls", fn))
            for lst in fams.values():
                if len(lst) < 2:
                    continue
                lst.sort()
                keep = lst[-1][2]
                for _, _, fn in lst[:-1]:
                    src, dst = Path(dirpath) / fn, Path(dirpath) / OBS / fn
                    moved.append({"file": to_rel(root, src), "keep": keep, "overwrote": dst.exists()})
                    if not dry_run:
                        dst.parent.mkdir(exist_ok=True)
                        os.replace(src, dst)
    return moved


# ---------------------------------------------------------------- accesos directos: actualizar y diagnosticar
def _new_raw(raw, newname):
    """Misma carpeta que decia el acceso directo (sin OBSOLETO), con el nombre nuevo."""
    r = raw.replace("/", "\\")
    parent = r.rsplit("\\", 1)[0] if "\\" in r else ""
    if parent.rsplit("\\", 1)[-1].upper() == OBS:
        parent = parent.rsplit("\\", 1)[0]
    return parent + "\\" + newname


def _backup_link(root, lnk, bdir):
    dest = Path(bdir) / "_accesos" / to_rel(root, lnk).replace("@", "_extra_", 1)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(lnk, dest)
    return str(dest)


@_writes
def retarget_links(root, via, orig, newpath, bdir):
    """Despues de una revision (el original paso a OBSOLETO y hay un archivo nuevo), apunta los accesos
    directos al archivo nuevo. via: lista que trae simulate(). Devuelve (hechos, manuales).
    'manuales' son los que hay que tocar a mano (.url, enlaces simbolicos, ubicacion dudosa)."""
    done, manual = [], []
    newpath, orig = Path(newpath), Path(orig)
    for v in via:
        if v["kind"] != "lnk" or v["status"] not in OK_STATES or not v.get("raw"):
            manual.append({"lnk": v["lnk"], "why": "no se actualiza solo (" + ("ubicación dudosa" if v["status"] == "aprox" else v["kind"]) + ")"})
            continue
        lnk = from_rel(root, v["lnk"])
        bk = None
        try:
            bk = _backup_link(root, lnk, bdir)
            new_raw = _new_raw(v["raw"], newpath.name)
            linkmod.write_link(lnk, new_raw)
            new_lnk = None
            if orig.stem in lnk.name:
                cand = lnk.with_name(lnk.name.replace(orig.stem, newpath.stem))
                if not cand.exists():
                    os.replace(lnk, cand)
                    new_lnk = to_rel(root, cand)
            done.append({"lnk": v["lnk"], "bk": bk, "new_lnk": new_lnk, "raw": new_raw})
        except Exception as e:
            try:                                    # dejar el acceso directo como estaba
                if bk and Path(bk).exists():
                    shutil.copy2(bk, lnk)
            except Exception:
                pass
            manual.append({"lnk": v["lnk"], "why": f"falló al actualizarlo: {e}"})
    return done, manual


@_writes
def restore_links(root, done):
    for d in done:
        lnk = from_rel(root, d["lnk"])
        if d.get("new_lnk"):
            nl = from_rel(root, d["new_lnk"])
            if nl.exists():
                nl.unlink()
        lnk.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(d["bk"], lnk)


def links_report(root):
    """Estado de todos los accesos directos y que hacer con los que no sirven."""
    d = discover(root)
    maps = settings.link_map
    items, summary = [], {}
    groups = {}
    for l in d["links"]:
        summary[l["status"]] = summary.get(l["status"], 0) + 1
        items.append({"lnk": to_rel(root, l["lnk"]), "kind": l["kind"], "is_dir": l["is_dir"], "raw": l["raw"],
                      "status": l["status"], "method": l["method"], "note": l["note"],
                      "target": to_rel(root, l["target"]) if l["target"] is not None and _nk(l["target"]) in
                      {**d["index"].files, **d["index"].dirs} else (str(l["target"]) if l["target"] else None)})
        raw = l["raw"] or ""
        if l["status"] in ("roto", "fuera", "aprox") and raw:
            r = raw.replace("/", "\\")
            parts = [x for x in r.split("\\") if x]
            if r.startswith("\\\\") and len(parts) >= 3:
                pre = "\\\\" + "\\".join(parts[:3])
            elif len(parts) >= 2 and parts[0].endswith(":"):
                pre = "\\".join(parts[:2])
            elif r.startswith("\\") and len(parts) >= 2:
                pre = "\\" + "\\".join(parts[:2])
            else:
                pre = ""
            if l.get("lost"):
                pre = l["lost"]
            if pre:
                g = groups.setdefault((pre, l["status"]), {"prefix": pre, "status": l["status"], "count": 0})
                g["count"] += 1
    sugg = []
    for g in sorted(groups.values(), key=lambda x: -x["count"]):
        if g["status"] == "fuera":
            g["hint"] = f"EXTRA_ROOTS=nombre={g['prefix']}  (si esa carpeta tiene que modificarse también)"
        elif g["status"] == "roto":
            g["hint"] = f"LINK_MAP={g['prefix']}={settings.docs_root}  (solo si esa carpeta existe en otro lugar, por ejemplo una copia; si no, son accesos rotos)"
        else:
            g["hint"] = f"LINK_MAP={g['prefix']}={settings.docs_root}  (para que esos accesos directos se lean sin adivinar)"
        sugg.append(g)
    order = {"roto": 0, "ambiguo": 1, "ilegible": 2, "fuera": 3, "drive": 4, "desactualizado": 5, "aprox": 6, "ok": 7}
    items.sort(key=lambda x: (order.get(x["status"], 9), x["lnk"]))
    return {"total": len(items), "summary": summary, "items": items, "suggestions": sugg,
            "link_map": [list(m) for m in maps], "extra_roots": [[n, str(p)] for n, p in settings.extra_roots],
            "docs_root": str(root)}


@_writes
def repair_links(root, bdir, dry_run=True):
    """Apunta a la revision vigente los accesos directos 'desactualizados' (los que quedaron en una
    revision vieja o en OBSOLETO). Guarda copia de cada uno en bdir."""
    d = discover(root)
    todo = [l for l in d["links"] if l["status"] == "desactualizado" and l["kind"] == "lnk" and not l["is_dir"]
            and l["target"] is not None]
    res = {"fixed": [], "failed": []}
    for l in todo:
        item = {"lnk": to_rel(root, l["lnk"]), "from": l["note"], "to": l["target"].name}
        if dry_run:
            res["fixed"].append(item)
            continue
        try:
            bk = _backup_link(root, l["lnk"], bdir)
            new_raw = _new_raw(l["raw"], l["target"].name)
            linkmod.write_link(l["lnk"], new_raw)
            res["fixed"].append(dict(item, bk=bk, raw=new_raw))
        except Exception as e:
            res["failed"].append(dict(item, why=str(e)))
    return res


# ---------------------------------------------------------------- avisos del sistema
_WARN = {}
_WARN_BUSY = set()


def _calc_warnings(root):
    out = []
    root = Path(root)
    if not root.exists():
        out.append({"code": "sin_carpeta", "text": f"No existe la carpeta de documentos ({root}). Revisá DOCS_ROOT en el archivo .env."})
    else:
        d = discover(root)
        xls = [p for p in d["files"] if p.suffix.lower() == ".xls"]
        if xls and not find_soffice():
            out.append({"code": "sin_libreoffice", "count": len(xls),
                        "text": f"Hay {len(xls)} planillas .xls y no se encontró LibreOffice: esas planillas no se leen, así que las búsquedas "
                                "y los cambios NO las incluyen. Instalá LibreOffice (libreoffice.org) en la computadora donde corre el portal."})
    return out


def system_warnings(root, ttl=120, wait=True):
    """Cosas que hacen que el portal 'no vea' planillas y que conviene avisar a todos.
    Con wait=False nunca espera: devuelve lo ultimo que se calculo (o nada si todavia no hay) y lo actualiza en segundo plano.
    Asi entrar al portal no depende de recorrer todas las carpetas."""
    import time
    key = str(root)
    hit = _WARN.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    if wait:
        out = _calc_warnings(root)
        _WARN[key] = (time.time(), out)
        return out
    if not Path(root).exists():
        return _calc_warnings(root)                 # esto es instantaneo
    if key not in _WARN_BUSY:
        _WARN_BUSY.add(key)

        def _bg():
            try:
                _WARN[key] = (time.time(), _calc_warnings(root))
            except Exception:
                _WARN[key] = (time.time(), [])
            finally:
                _WARN_BUSY.discard(key)
        threading.Thread(target=_bg, daemon=True, name="avisos-sistema").start()
    return hit[1] if hit else []
