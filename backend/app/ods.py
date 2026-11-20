"""Lectura y edicion de .ods tocando solo el XML (content.xml): se conservan estilos, anchos,
imagenes y todo lo demas del archivo. Los hijos repetidos (number-columns/rows-repeated) se
manejan sin expandir la hoja entera."""
import copy
import io
import re
import zipfile
import xml.etree.ElementTree as etree      # solo biblioteca estandar: no hace falta instalar nada

NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}
T = lambda p, n: "{%s}%s" % (NS[p], n)
ROW, CELL, COVERED, TABLE, TEXTP = T("table", "table-row"), T("table", "table-cell"), T("table", "covered-table-cell"), T("table", "table"), T("text", "p")
REP_C, REP_R = T("table", "number-columns-repeated"), T("table", "number-rows-repeated")
VTYPE, VAL, FORMULA, NAME = T("office", "value-type"), T("office", "value"), T("table", "formula"), T("table", "name")
SKIP_REP = 1000   # celdas/filas vacias repetidas mas alla de esto no se recorren


def col_letter(n):  # 1 -> A
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def cell_text(cell):
    return "\n".join("".join(p.itertext()) for p in cell.findall(TEXTP)).strip()


def _rows(table):
    """Filas de la hoja en orden, entrando a header-rows y row-groups."""
    for ch in table:
        if ch.tag == ROW:
            yield ch
        elif ch.tag.endswith("}table-header-rows") or ch.tag.endswith("}table-row-group") or ch.tag.endswith("}table-rows"):
            for r in _rows(ch):
                yield r


def iter_cells(table):
    """Genera (fila, col, elemento_fila, elemento_celda, texto) para celdas con contenido."""
    r = 0
    for row in _rows(table):
        rrep = int(row.get(REP_R, "1"))
        r += 1
        c = 0
        for cell in list(row):
            if cell.tag not in (CELL, COVERED):
                continue
            crep = int(cell.get(REP_C, "1"))
            c += 1
            txt = cell_text(cell) if cell.tag == CELL else ""
            if txt and crep <= SKIP_REP:
                for k in range(min(crep, 50)):          # celda repetida con contenido: una por columna
                    yield r, c + k, row, cell, txt
            c += crep - 1
        r += rrep - 1


class OdsDoc:
    kind = "ods"
    out_ext = ".ods"

    def __init__(self, path):
        self.path = path
        with zipfile.ZipFile(path) as z:
            self._xml = z.read("content.xml")
        # conservar los prefijos originales (office:, table:, text:, style:, fo:...) al volver a escribir
        for _ev, (prefix, uri) in etree.iterparse(io.BytesIO(self._xml), events=("start-ns",)):
            if prefix:
                etree.register_namespace(prefix, uri)
        self.root = etree.fromstring(self._xml)
        self.spreadsheet = self.root.find(".//" + T("office", "spreadsheet"))
        self.sheets = self.root.findall(".//" + TABLE)

    # ---- lectura ----
    def cells(self):
        out = []
        for t in self.sheets:
            name = t.get(NAME)
            if name == "Control de cambios":
                continue
            for r, c, _row, _cell, txt in iter_cells(t):
                out.append((name, f"{col_letter(c)}{r}", txt))
        return out

    # ---- edicion ----
    def _qty_col(self, table):
        for r, c, _row, _cell, txt in iter_cells(table):
            if r > 15:
                break
            if txt.lower().startswith("cant"):
                return c
        return None

    @staticmethod
    def _set_text(cell, value):
        ps = cell.findall(TEXTP)
        for extra in ps[1:]:
            cell.remove(extra)
        if not ps:
            p = etree.SubElement(cell, TEXTP)
        else:
            p = ps[0]
            for ch in list(p):
                p.remove(ch)
        p.text = value

    @staticmethod
    def _cell_at(row, col):
        """Devuelve la celda de la columna 'col' dentro de la fila, partiendo grupos repetidos."""
        c = 0
        for cell in list(row):
            if cell.tag not in (CELL, COVERED):
                continue
            crep = int(cell.get(REP_C, "1"))
            if c + 1 <= col <= c + crep:
                if crep == 1:
                    return cell
                before, after = col - c - 1, c + crep - col
                parts = []
                if before:
                    b = copy.deepcopy(cell); b.set(REP_C, str(before)); parts.append(b)
                mid = copy.deepcopy(cell)
                if REP_C in mid.attrib:
                    del mid.attrib[REP_C]
                parts.append(mid)
                if after:
                    a = copy.deepcopy(cell); a.set(REP_C, str(after)); parts.append(a)
                idx = list(row).index(cell)
                row.remove(cell)
                for k, part in enumerate(parts):
                    row.insert(idx + k, part)
                return mid
            c += crep
        return None

    @staticmethod
    def _invalidate_formulas(table):
        """Quita el resultado guardado de las formulas de la hoja para que LibreOffice las recalcule al abrir
        (si no, un total como =cantidad*precio seguiria mostrando el valor viejo despues de cambiar la cantidad)."""
        calcext = "{urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0}value-type"
        for cell in table.iter(CELL):
            if cell.get(FORMULA):
                for a in (VAL, VTYPE, calcext, T("office", "string-value"), T("office", "boolean-value"), T("office", "date-value")):
                    if a in cell.attrib:
                        del cell.attrib[a]
                for p_ in cell.findall(TEXTP):
                    cell.remove(p_)

    def apply_change(self, old, new, factor=None):
        old_u = old.strip().upper()
        changes = []
        for t in self.sheets:
            if t.get(NAME) == "Control de cambios":
                continue
            qcol = self._qty_col(t) if factor not in (None, 1, 1.0) else None
            hits = [(r, c, row, cell) for r, c, row, cell, txt in iter_cells(t) if txt.upper() == old_u and not cell.get(FORMULA)]
            edited, qty_rows = set(), set()
            for r, c, row, cell in hits:
                if id(cell) not in edited:
                    edited.add(id(cell))
                    self._set_text(cell, new)
                    if cell.get(VTYPE) != "string":
                        cell.set(VTYPE, "string")
                    for a in (VAL, T("office", "string-value")):
                        if a in cell.attrib:
                            del cell.attrib[a]
                item = {"sheet": t.get(NAME), "cell": f"{col_letter(c)}{r}", "before": old_u, "after": new}
                if qcol and id(row) not in qty_rows:
                    qty_rows.add(id(row))
                    q = self._cell_at(row, qcol)
                    if q is not None and q.get(VTYPE) == "float" and q.get(VAL) is not None:
                        b = float(q.get(VAL)); a_ = b * factor
                        sv = repr(int(a_)) if a_ == int(a_) else repr(a_)
                        q.set(VAL, sv); self._set_text(q, sv)
                        item["qty"] = {"before": b, "after": a_}
                changes.append(item)
            if qty_rows:
                self._invalidate_formulas(t)
        return changes

    def _put(self, row, col, text, like=None):
        """Escribe texto en la columna 'col' de la fila, agregando celdas vacias si la fila es mas corta."""
        total = sum(int(c.get(REP_C, "1")) for c in row if c.tag in (CELL, COVERED))
        if total < col:
            pad = etree.SubElement(row, CELL)
            if col - total > 1:
                pad.set(REP_C, str(col - total))
        cell = self._cell_at(row, col)
        if like is not None and like.get(T("table", "style-name")):
            cell.set(T("table", "style-name"), like.get(T("table", "style-name")))
        cell.set(VTYPE, "string")
        for a in (VAL, T("office", "string-value")):
            if a in cell.attrib:
                del cell.attrib[a]
        self._set_text(cell, text)

    def add_suggestions(self, code, alts, header="Sugerencias"):
        """Igual que en .xlsx: columna 'Sugerencias' con los codigos sugeridos en cada fila donde esta 'code'."""
        code = code.strip().upper()
        alts = [a.strip().upper() for a in alts]
        changes = []
        for t in self.sheets:
            if t.get(NAME) == "Control de cambios":
                continue
            cells = list(iter_cells(t))
            hits = [x for x in cells if x[4].upper() == code]
            if not hits:
                continue
            hr = next((r for r, c, _w, _c, txt in cells if r <= 15 and txt.lower().startswith("cant")), None)
            if hr is None:
                hr = min(r for r, *_ in cells)
            hrow = next(row for r, c, row, _c, txt in cells if r == hr)
            col = next((c for r, c, _w, _c, txt in cells if r == hr and txt.lower().startswith("sugerencia")), None)
            if col is None:
                last = max(c for _r, c, *_ in cells)
                col = last + 1
                prev = self._cell_at(hrow, col - 1) if col > 1 else None
                self._put(hrow, col, header, like=prev)
            seen = set()
            for r, c, row, _cell, _txt in hits:
                if r <= hr or id(row) in seen:
                    continue
                seen.add(id(row))
                cur_cell = self._cell_at(row, col)
                cur_txt = cell_text(cur_cell) if cur_cell is not None else ""
                cur = [x.strip() for x in cur_txt.split(",") if x.strip()]
                have = {x.upper() for x in cur}
                merged = cur + [a for a in alts if a not in have]
                if merged != cur:
                    self._put(row, col, ", ".join(merged))
                    changes.append({"sheet": t.get(NAME), "cell": f"{col_letter(col)}{r}", "before": ", ".join(cur), "after": ", ".join(merged)})
        return changes

    def write_control(self, entry):
        header = ["Revisión", "Qué cambió", "Motivo", "Quién lo pidió", "Quién lo autorizó", "Fecha", "Pedido"]
        values = [entry["rev"], entry["what"], entry.get("why", ""), entry["by"], entry["auth"], entry["date"], entry["order"]]
        sheet = next((t for t in self.sheets if t.get(NAME) == "Control de cambios"), None)
        if sheet is None:
            sheet = etree.Element(TABLE)
            sheet.set(NAME, "Control de cambios")
            col = etree.SubElement(sheet, T("table", "table-column"))
            col.set(REP_C, "7")
            self.spreadsheet.append(sheet)
            self.sheets.append(sheet)
            self._add_row(sheet, header)
        self._add_row(sheet, values)

    @staticmethod
    def _add_row(sheet, values):
        row = etree.SubElement(sheet, ROW)
        for v in values:
            c = etree.SubElement(row, CELL)
            c.set(VTYPE, "string")
            etree.SubElement(c, TEXTP).text = str(v)

    def save(self, dest):
        data = etree.tostring(self.root, encoding="UTF-8", xml_declaration=True)
        # ElementTree borra las declaraciones de espacios de nombres que no usa ninguna etiqueta (por ejemplo xmlns:of,
        # que solo aparece dentro del texto de las formulas "of:=[.C8]*[.D8]"). Sin eso LibreOffice da Err:510 en
        # todas las formulas. Se vuelve a poner la etiqueta raiz original, que no cambia.
        m_old = re.search(rb"<[\w.-]+:document-content\b[^>]*>", self._xml)
        if m_old:
            data = re.sub(rb"<[\w.-]+:document-content\b[^>]*>", lambda _m: m_old.group(0), data, count=1)
        with zipfile.ZipFile(self.path) as zin, zipfile.ZipFile(dest, "w") as zout:
            names = zin.namelist()
            if "mimetype" in names:     # debe ir primero y sin comprimir
                zout.writestr(zipfile.ZipInfo("mimetype"), zin.read("mimetype"), compress_type=zipfile.ZIP_STORED)
            for n in names:
                if n == "mimetype":
                    continue
                zout.writestr(n, data if n == "content.xml" else zin.read(n), compress_type=zipfile.ZIP_DEFLATED)
