"""Accesos directos: leer (.lnk de Windows, .url, enlaces simbolicos) y escribir .lnk.

El lector de .lnk es propio (formato MS-SHLLINK) y funciona en cualquier sistema, asi que se puede
probar sin Windows. En Windows, si un .lnk no se puede leer o hay que cambiarle el destino, se usa
ademas el objeto WScript.Shell (PowerShell) que es lo mas fiel."""
import json
import os
import struct
import subprocess
import tempfile
from pathlib import Path

HAS_IDLIST, HAS_LINKINFO, HAS_NAME, HAS_REL, HAS_WD, HAS_ARGS, HAS_ICON, IS_UNICODE = 1, 2, 4, 8, 16, 32, 64, 128


def _cstr(data, off, unicode_=False):
    if unicode_:
        end = off
        while end + 1 < len(data) and data[end:end + 2] != b"\x00\x00":
            end += 2
        return data[off:end].decode("utf-16-le", "replace")
    end = data.find(b"\x00", off)
    end = len(data) if end < 0 else end
    return data[off:end].decode("cp1252", "replace")


def _join(base, suffix):
    if not suffix:
        return base
    if not base:
        return suffix
    if base.endswith("\\") or suffix.startswith("\\"):
        return base + suffix
    return base + "\\" + suffix


def parse_lnk(data):
    """Devuelve {"raws": [rutas posibles], "rel": ruta relativa guardada, "is_dir": bool}.
    'raws' son las formas en que el .lnk guarda el destino (ruta local, \\\\servidor\\recurso, letra de unidad)."""
    if len(data) < 76 or struct.unpack_from("<I", data, 0)[0] != 0x4C:
        raise ValueError("no es un .lnk valido")
    flags = struct.unpack_from("<I", data, 0x14)[0]
    attrs = struct.unpack_from("<I", data, 0x18)[0]
    pos = 0x4C
    if flags & HAS_IDLIST:
        pos += 2 + struct.unpack_from("<H", data, pos)[0]
    raws = []
    if flags & HAS_LINKINFO:
        li = pos
        size, hsize, lflags, _vol, base_off, net_off, suf_off = struct.unpack_from("<7I", data, li)
        base_u = suf_u = None
        if hsize >= 0x24:
            bo_u, so_u = struct.unpack_from("<2I", data, li + 28)
            if bo_u:
                base_u = _cstr(data, li + bo_u, True)
            if so_u:
                suf_u = _cstr(data, li + so_u, True)
        suffix = suf_u if suf_u is not None else (_cstr(data, li + suf_off) if suf_off else "")
        if lflags & 1:
            base = base_u if base_u is not None else (_cstr(data, li + base_off) if base_off else "")
            if base:
                raws.append(_join(base, suffix))
        if lflags & 2 and net_off:
            cn = li + net_off
            n_flags = struct.unpack_from("<I", data, cn + 4)[0]
            noff, doff = struct.unpack_from("<2I", data, cn + 8)
            net = dev = ""
            if noff > 0x14:
                no_u, do_u = struct.unpack_from("<2I", data, cn + 20)
                net = _cstr(data, cn + no_u, True) if no_u else ""
                dev = _cstr(data, cn + do_u, True) if do_u else ""
            if not net and noff:
                net = _cstr(data, cn + noff)
            if not dev and doff and (n_flags & 1):
                dev = _cstr(data, cn + doff)
            if net:
                raws.append(_join(net, suffix))
            if dev:
                raws.append(_join(dev, suffix))
        pos = li + size
    unic = bool(flags & IS_UNICODE)
    strings = {}
    for bit, key in ((HAS_NAME, "name"), (HAS_REL, "rel"), (HAS_WD, "wd"), (HAS_ARGS, "args"), (HAS_ICON, "icon")):
        if flags & bit:
            if pos + 2 > len(data):
                break
            n = struct.unpack_from("<H", data, pos)[0]
            pos += 2
            if unic:
                strings[key] = data[pos:pos + 2 * n].decode("utf-16-le", "replace")
                pos += 2 * n
            else:
                strings[key] = data[pos:pos + n].decode("cp1252", "replace")
                pos += n
    seen = []
    for r in raws:
        if r and r not in seen:
            seen.append(r)
    return {"raws": seen, "rel": strings.get("rel", ""), "is_dir": bool(attrs & 0x10)}


def build_lnk(target, is_dir=False):
    """Arma un .lnk minimo (con LinkInfo) a una ruta de Windows. Se usa fuera de Windows y de respaldo."""
    target = target.replace("/", "\\")
    unc = target.startswith("\\\\")
    hdr = bytearray(76)
    struct.pack_into("<I", hdr, 0, 0x4C)
    hdr[4:20] = bytes([0x01, 0x14, 0x02, 0, 0, 0, 0, 0, 0xC0, 0, 0, 0, 0, 0, 0, 0x46])
    struct.pack_into("<I", hdr, 0x14, HAS_LINKINFO | IS_UNICODE)
    struct.pack_into("<I", hdr, 0x18, 0x10 if is_dir else 0x20)
    struct.pack_into("<I", hdr, 0x3C, 1)                       # ShowCommand: normal
    a = lambda s: s.encode("cp1252", "replace") + b"\x00"
    u = lambda s: s.encode("utf-16-le") + b"\x00\x00"
    HS = 0x24
    if unc:
        parts = target[2:].split("\\", 2)
        net = "\\\\" + "\\".join(parts[:2])
        suffix = parts[2] if len(parts) > 2 else ""
        cnrl = struct.pack("<IIIII", 0x14 + len(a(net)), 0, 0x14, 0, 0) + a(net)
        body, off = bytearray(), HS
        cn_off = off; body += cnrl; off += len(cnrl)
        suf_off = off; body += a(suffix); off += len(a(suffix))
        suf_u_off = off; body += u(suffix)
        total = HS + len(body)
        info = struct.pack("<9I", total, HS, 2, 0, 0, cn_off, suf_off, 0, suf_u_off) + bytes(body)
    else:
        base, suffix = target, ""
        vol_label = b"\x00"
        vol = struct.pack("<IIII", 0x10 + len(vol_label), 3, 0, 0x10) + vol_label
        body, off = bytearray(), HS
        vol_off = off; body += vol; off += len(vol)
        base_off = off; body += a(base); off += len(a(base))
        suf_off = off; body += a(suffix); off += len(a(suffix))
        base_u_off = off; body += u(base); off += len(u(base))
        suf_u_off = off; body += u(suffix)
        total = HS + len(body)
        info = struct.pack("<9I", total, HS, 1, vol_off, base_off, 0, suf_off, base_u_off, suf_u_off) + bytes(body)
    return bytes(hdr) + info + b"\x00\x00\x00\x00"


def parse_url(data):
    """.url de Windows (InternetShortcut): solo interesan los que apuntan a file:///."""
    txt = data.decode("utf-8-sig", "replace") if isinstance(data, bytes) else data
    for line in txt.splitlines():
        if line.strip().upper().startswith("URL="):
            url = line.strip()[4:]
            if url.lower().startswith("file:"):
                from urllib.parse import unquote
                p = unquote(url[5:])
                if len(p) > 3 and p[0] == "/" and p[2] == ":":
                    p = p[1:]
                if p.startswith("///"):
                    p = p[3:]
                elif p.startswith("//"):
                    p = "\\\\" + p[2:]
                return {"raws": [p.replace("/", "\\")], "rel": "", "is_dir": False}
            return {"raws": [], "rel": "", "is_dir": False, "url": url}
    return {"raws": [], "rel": "", "is_dir": False}


def parse_gsheet(data):
    """.gsheet / .gdoc que crea Google Drive para escritorio: es un JSON con la direccion del documento."""
    try:
        j = json.loads(data.decode("utf-8-sig", "replace"))
    except Exception:
        raise ValueError("no es un acceso de Google Drive")
    url = j.get("url") or (("https://docs.google.com/spreadsheets/d/" + j["doc_id"]) if j.get("doc_id") else "")
    if not url:
        raise ValueError("el acceso de Google Drive no tiene direccion")
    return {"raws": [], "rel": "", "is_dir": False, "url": url}


def read_link(path):
    """Lee un acceso directo ya existente en disco. Lanza ValueError si no se entiende."""
    path = Path(path)
    ext = path.suffix.lower()
    data = path.read_bytes()
    if ext == ".lnk":
        return parse_lnk(data)
    if ext == ".url":
        return parse_url(data)
    if ext in (".gsheet", ".gdoc"):
        return parse_gsheet(data)
    raise ValueError("tipo de acceso directo desconocido")


# ---------------------------------------------------------------- PowerShell (solo Windows)
def _ps(script, env, timeout=60):
    if os.name != "nt":
        return None
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, timeout=timeout, env=dict(os.environ, **env))
    except Exception:
        return None
    return r if r.returncode == 0 else None


def ps_read(paths):
    """{ruta_del_lnk: destino} usando Windows. Para los .lnk que el lector propio no entendio."""
    if os.name != "nt" or not paths:
        return {}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump([str(p) for p in paths], f)
    script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
              "$w=New-Object -ComObject WScript.Shell;"
              "$l=Get-Content -Raw -Encoding UTF8 -LiteralPath $env:SV_LIST|ConvertFrom-Json;"
              "$o=@(); foreach($p in $l){$s=$w.CreateShortcut($p);$o+=[pscustomobject]@{l=$p;t=$s.TargetPath}};"
              "ConvertTo-Json -InputObject $o -Compress")
    r = _ps(script, {"SV_LIST": f.name})
    try:
        os.unlink(f.name)
    except OSError:
        pass
    if not r:
        return {}
    try:
        arr = json.loads(r.stdout.decode("utf-8", "replace"))
    except Exception:
        return {}
    arr = arr if isinstance(arr, list) else [arr]
    return {x["l"]: x["t"] for x in arr if x.get("t")}


_WINDOWS = os.name == "nt"


def _ps_write(path, target):
    script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:SV_LNK);"
              "$s.TargetPath=$env:SV_TGT;$s.Save()")
    return _ps(script, {"SV_LNK": str(path), "SV_TGT": target}) is not None


def _ultimo(ruta):
    return ruta.replace("/", "\\").rstrip("\\").rsplit("\\", 1)[-1].lower()


def write_link(path, target, is_dir=False):
    """Cambia (o crea) el destino de un .lnk. Primero con Windows; si no, con el escritor propio.
    Despues relee con el lector del portal y confirma; si no coincide lanza OSError.
    Windows, cuando no puede resolver la ruta (por ejemplo la unidad Z: no esta conectada en esa computadora),
    a veces actualiza una parte del acceso y deja la otra con el destino viejo: el portal lo volveria a ver
    como desactualizado. En ese caso se reescribe con el escritor propio."""
    path = Path(path)
    want = _ultimo(target)
    done = _WINDOWS and _ps_write(path, target)

    def apunta():
        """True/False si el portal lo lee y apunta (o no) al archivo nuevo; None si no lo puede leer."""
        try:
            raws = read_link(path)["raws"]
        except Exception:
            return None
        return (_ultimo(raws[0]) == want) if raws else None

    if not done:
        path.write_bytes(build_lnk(target, is_dir))
    elif apunta() is False:
        path.write_bytes(build_lnk(target, is_dir))
    ok = apunta()
    if ok is None and _WINDOWS:
        ok = _ultimo(ps_read([path]).get(str(path), "")) == want
    if not ok:
        raise OSError("no se pudo confirmar el nuevo destino del acceso directo")
