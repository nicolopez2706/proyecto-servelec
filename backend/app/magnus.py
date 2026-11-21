"""Control contra Magnus por archivo exportado.

Magnus no tiene conexión directa con el portal: alguien exporta sus artículos a una planilla (o CSV) y deja el archivo en
la ruta MAGNUS_EXPORT. Este módulo la lee y la compara con lo que hay en las planillas de documentación y en el catálogo
del portal, y arma un Excel con tres listas: códigos que usan las planillas y NO existen en Magnus, códigos con
descripción distinta, y códigos que están solo en Magnus. No modifica nada: solo informa."""
import csv
import datetime as dt
import json
import os
import re
import unicodedata
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import engine, ods as odsmod
from .config import settings
from .db import connect
from .permissions import require

MAX_INFORMES = 12
_HEAD_COD = re.compile(r"(?i)^\s*(c[oó]d(igo|\.)?(\s+(magnus|art[ií]culo|producto|material|item))?|art[ií]culo|item|material|referencia|code)\s*$")
_HEAD_DESC = re.compile(r"(?i)^\s*(descripci[oó]n|descripcion\s+\w+|detalle|denominaci[oó]n|nombre|description)\s*$")
EXTS_EXPORT = {".xlsx", ".xlsm", ".xls", ".ods", ".csv"}


class MagnusError(ValueError):
    """Problema con la exportación (no configurada, no se encuentra, no se entiende): el mensaje se le muestra al usuario."""


# ------------------------------------------------------------------ leer la exportación
def archivo_export():
    """Ruta del archivo a usar: MAGNUS_EXPORT, o el más nuevo de esa carpeta."""
    raw = settings.magnus_export
    if not raw:
        raise MagnusError("Todavía no está configurada la exportación de Magnus: poné la ruta en MAGNUS_EXPORT (archivo .env).")
    p = Path(raw)
    if p.is_dir():
        cand = [f for f in p.iterdir() if f.is_file() and f.suffix.lower() in EXTS_EXPORT and not f.name.startswith(("~$", "."))]
        if not cand:
            raise MagnusError(f"No hay ninguna planilla ni CSV en la carpeta {p}.")
        return max(cand, key=lambda f: f.stat().st_mtime_ns)
    if not p.is_file():
        raise MagnusError(f"No se encuentra el archivo de Magnus: {p}")
    return p


def _filas_csv(path):
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            texto = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    muestra = texto[:4096]
    delim = max(";,\t|", key=muestra.count)
    return [[c.strip() for c in fila] for fila in csv.reader(texto.splitlines(), delimiter=delim)]


def _filas_xlsx(path):
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        return [["" if c is None else str(c).strip() for c in fila] for fila in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def _filas_ods(path):
    grid, maxr, maxc = {}, 0, 0
    doc = odsmod.OdsDoc(path)
    primera = doc.sheets[0].get(odsmod.NAME) if doc.sheets else None
    for hoja, coord, txt in doc.cells():
        if hoja != primera:
            continue
        col_l = re.match(r"[A-Z]+", coord).group(0)
        r, c = int(coord[len(col_l):]), 0
        for ch in col_l:
            c = c * 26 + ord(ch) - 64
        grid[(r, c)] = txt
        maxr, maxc = max(maxr, r), max(maxc, c)
    return [[grid.get((r, c), "") for c in range(1, maxc + 1)] for r in range(1, maxr + 1)]


def filas_export(path):
    ext = Path(path).suffix.lower()
    if ext == ".csv":
        return _filas_csv(Path(path))
    if ext in engine.XLSX_EXTS:
        return _filas_xlsx(path)
    if ext == ".ods":
        return _filas_ods(path)
    if ext == ".xls":
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="magnus_"))
        try:
            return _filas_xlsx(engine.convert_to_xlsx([Path(path)], tmp)[Path(path)])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    raise MagnusError(f"Formato {ext} no soportado para la exportación de Magnus.")


def _col_por_nombre(fila, pedido):
    """Índice de columna: por letra («B») o por texto del encabezado."""
    if re.fullmatch(r"[A-Za-z]{1,2}", pedido):
        n = 0
        for ch in pedido.upper():
            n = n * 26 + ord(ch) - 64
        return n - 1
    for i, c in enumerate(fila):
        if c.strip().lower() == pedido.lower():
            return i
    return None


def leer_export(path):
    """{código: descripción}. Detecta la fila de encabezados y las columnas de código y descripción
    (o usa MAGNUS_COL_CODIGO / MAGNUS_COL_DESC)."""
    filas = filas_export(path)
    if not filas:
        raise MagnusError("La exportación de Magnus está vacía.")
    cc, cd, ini = None, None, None
    for i, fila in enumerate(filas[:25]):
        c = _col_por_nombre(fila, settings.magnus_col_codigo) if settings.magnus_col_codigo else next(
            (j for j, x in enumerate(fila) if _HEAD_COD.match(x)), None)
        if c is None:
            continue
        d = _col_por_nombre(fila, settings.magnus_col_desc) if settings.magnus_col_desc else next(
            (j for j, x in enumerate(fila) if j != c and _HEAD_DESC.match(x)), None)
        cc, cd, ini = c, d, i + 1
        break
    if cc is None:
        raise MagnusError("No encontré la columna del código en la exportación de Magnus. Indicá el nombre del encabezado en "
                          "MAGNUS_COL_CODIGO (y MAGNUS_COL_DESC para la descripción) en el archivo .env.")
    out = {}
    for fila in filas[ini:]:
        cod = engine.norm(fila[cc]) if cc < len(fila) else ""
        if not cod or " " in cod:
            continue
        desc = fila[cd].strip() if cd is not None and cd < len(fila) else ""
        out.setdefault(cod, desc)
    if not out:
        raise MagnusError("La exportación de Magnus no tiene códigos debajo del encabezado.")
    return out


# ------------------------------------------------------------------ comparar
def _tokens(texto):
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().lower()
    return set(re.findall(r"[a-z0-9]+", t))


def descripcion_distinta(a, b):
    """True si las dos descripciones no se parecen (ignora mayúsculas, tildes, orden y que una agregue detalles)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    if ta <= tb or tb <= ta:
        return False
    return len(ta & tb) / len(ta | tb) < 0.5


def comparar(magnus, usados, catalogo):
    """magnus {cod: desc}; usados = engine.code_usage; catalogo {cod: desc}. No toca nada."""
    faltan, distinta = [], []
    for cod in sorted(set(usados) | set(catalogo)):
        u, c = usados.get(cod), catalogo.get(cod)
        desc = (c if c else (u["desc"] if u else "")) or ""
        if cod not in magnus:
            origen = "Planillas y catálogo del portal" if (u and cod in catalogo) else ("Planillas" if u else "Catálogo del portal")
            faltan.append({"code": cod, "desc": desc, "origen": origen, "cells": u["cells"] if u else 0, "folders": u["folders"] if u else 0})
        elif desc and magnus[cod] and descripcion_distinta(desc, magnus[cod]):
            distinta.append({"code": cod, "desc_portal": desc, "desc_magnus": magnus[cod]})
    solo = [{"code": c, "desc": magnus[c]} for c in sorted(set(magnus) - set(usados) - set(catalogo))]
    return {"faltan": faltan, "distinta": distinta, "solo_magnus": solo}


# ------------------------------------------------------------------ informe en Excel
def _hoja(wb, titulo, cols, filas, ancho):
    ws = wb.create_sheet(titulo)
    ws.append(cols)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1E3A5F")
    for f in filas:
        ws.append(f)
    for i, w in enumerate(ancho, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    if filas:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{len(filas) + 1}"
    return ws


def escribir_informe(res, resumen, dest):
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen"
    ws.append(["Control contra Magnus"])
    ws["A1"].font = Font(bold=True, size=14)
    for k, v in resumen["lineas"]:
        ws.append([k, v])
    ws.column_dimensions["A"].width = 52
    ws.column_dimensions["B"].width = 60
    for r in ws.iter_rows(min_row=2):
        r[0].alignment = Alignment(wrap_text=True, vertical="top")
    _hoja(wb, "Faltan en Magnus", ["Código", "Descripción", "Dónde está", "Celdas", "Carpetas"],
          [[x["code"], x["desc"], x["origen"], x["cells"], x["folders"]] for x in res["faltan"]], (22, 60, 30, 10, 10))
    _hoja(wb, "Descripción distinta", ["Código", "Según el portal / planillas", "Según Magnus"],
          [[x["code"], x["desc_portal"], x["desc_magnus"]] for x in res["distinta"]], (22, 55, 55))
    _hoja(wb, "Solo en Magnus", ["Código", "Descripción"], [[x["code"], x["desc"]] for x in res["solo_magnus"]], (22, 70))
    tmp = Path(str(dest) + ".tmp")
    wb.save(tmp)
    os.replace(tmp, dest)


def _guardar_y_limpiar(res, resumen):
    d = settings.magnus_dir
    d.mkdir(parents=True, exist_ok=True)
    nombre = f"control_magnus_{dt.datetime.now():%Y-%m-%d_%H%M%S}.xlsx"
    escribir_informe(res, resumen, d / nombre)
    (d / "ultimo.json").write_text(json.dumps(dict(resumen, informe=nombre), ensure_ascii=False), encoding="utf-8")
    viejos = sorted(d.glob("control_magnus_*.xlsx"))[:-MAX_INFORMES]
    for v in viejos:
        v.unlink(missing_ok=True)
    return nombre


# ------------------------------------------------------------------ lo que usa el portal
def _catalogo():
    con = connect()
    try:
        return {r["code"]: (r["desc"] or "") for r in con.execute("SELECT code, desc FROM catalog")}
    finally:
        con.close()


def estado(user):
    require(user, "magnus")
    out = {"configurado": bool(settings.magnus_export), "ruta": settings.magnus_export, "archivo": None, "archivo_fecha": None,
           "error": None, "ultimo": None, "informes": []}
    if out["configurado"]:
        try:
            f = archivo_export()
            out["archivo"], out["archivo_fecha"] = f.name, dt.datetime.fromtimestamp(f.stat().st_mtime).strftime("%d/%m/%Y %H:%M")
        except MagnusError as e:
            out["error"] = str(e)
    uj = settings.magnus_dir / "ultimo.json"
    if uj.exists():
        try:
            out["ultimo"] = json.loads(uj.read_text(encoding="utf-8"))
        except Exception:
            pass
    out["informes"] = sorted((p.name for p in settings.magnus_dir.glob("control_magnus_*.xlsx")), reverse=True) if settings.magnus_dir.exists() else []
    return out


def ejecutar(user):
    """Compara y deja el informe. Solo lee: se puede usar aunque el portal esté en solo lectura."""
    require(user, "magnus_run")
    path = archivo_export()
    magnus = leer_export(path)
    usados = engine.code_usage(settings.docs_root)
    res = comparar(magnus, usados, _catalogo())
    ahora = dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    resumen = {"fecha": ahora, "archivo": path.name, "por": user["name"], "en_magnus": len(magnus), "en_planillas": len(usados),
               "faltan": len(res["faltan"]), "distinta": len(res["distinta"]), "solo_magnus": len(res["solo_magnus"]),
               "primeros_faltantes": [x["code"] for x in res["faltan"][:10]], "portal_url": settings.portal_url + "/#/magnus"}
    resumen["lineas"] = [("Fecha del control", ahora), ("Archivo de Magnus usado", path.name), ("Quién lo pidió", user["name"]),
                         ("Códigos en Magnus", len(magnus)), ("Códigos distintos en las planillas", len(usados)),
                         ("Faltan en Magnus (usados en planillas o catálogo)", len(res["faltan"])),
                         ("Con descripción distinta", len(res["distinta"])), ("Solo en Magnus (no aparecen en planillas)", len(res["solo_magnus"])),
                         ("Qué hacer", "Revisá «Faltan en Magnus»: son los candidatos a dar de alta. «Descripción distinta» puede ser un "
                                       "error de carga en un lado u en otro. Este control no modifica nada.")]
    resumen["informe"] = _guardar_y_limpiar(res, resumen)
    from .services import audit
    con = connect()
    try:
        audit(con, user["email"], "Control contra Magnus", "",
              f"{resumen['faltan']} faltan en Magnus, {resumen['distinta']} con descripción distinta, {resumen['solo_magnus']} solo en Magnus")
        con.commit()
    finally:
        con.close()
    resumen.pop("lineas")
    return resumen


def informe(user, nombre):
    """Informe en Excel como base64 (la API devuelve JSON; el navegador lo convierte en descarga)."""
    require(user, "magnus")
    if not re.fullmatch(r"control_magnus_[\d_-]+\.xlsx", nombre or ""):
        raise MagnusError("Nombre de informe inválido.")
    p = settings.magnus_dir / nombre
    if not p.is_file():
        raise MagnusError("Ese informe ya no existe.")
    import base64
    return {"name": nombre, "base64": base64.b64encode(p.read_bytes()).decode("ascii")}
