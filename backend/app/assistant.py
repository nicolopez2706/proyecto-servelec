"""Asistente del portal: entiende pedidos escritos en lenguaje natural y los convierte en acciones del portal.

Cómo está pensado (importante para quien lo mantenga):
  1. interpretar(texto)  -> decide QUÉ quiso decir la persona ("cambiar X por Y", "buscar...", "mis pedidos"...).
                            Hoy lo hace con reglas: gratis, funciona sin internet y NINGÚN dato sale de la computadora.
                            Es la única pieza que se cambiaría si algún día se conecta un modelo de lenguaje (Gemini,
                            Claude...): el modelo solo devolvería la misma "intención" estructurada.
  2. responder(...)      -> ejecuta esa intención con las MISMAS funciones y permisos que usa la pantalla del portal.
  3. El asistente solo puede: buscar, mostrar información, simular un cambio, crear un pedido (que después aprueba otra
     persona), avisar que falta un código y contar el estado de los pedidos. NO existe ninguna ruta de este archivo que
     apruebe, rechace, revierta, limpie ni administre: eso lo hace siempre una persona desde Pedidos.
  4. Crear un pedido o un aviso nunca ocurre por lo que "entendió" el asistente: siempre hace falta que la persona apriete
     el botón de confirmar (la acción llega como {"do": ..., "params": ...} y se valida de nuevo en el portal)."""
import re
import unicodedata

from . import engine, services
from .permissions import can, require

MAX_TEXTO = 600

# Las únicas acciones que el asistente puede ejecutar cuando la persona confirma con un botón.
ACCIONES = ("create_order", "request_code")

_CODIGO = r"[A-Za-z0-9][A-Za-z0-9._/\-]*"           # DIS206AA, X-1, TOR-M8
_NO_ES_CODIGO = {"EL", "LA", "LOS", "LAS", "UN", "UNA", "CODIGO", "POR", "A", "CON", "DE", "DEL", "X", "Y", "O", "QUE", "CUAL"}
_ARTICULO = r"(?:(?:el|la|al|del|un|una)\s+)?(?:(?:codigo|insumo|material|item|producto)\s+)?"
_CAMBIO = re.compile(
    rf"\b(?:cambi\w*|reemplaz\w*|sustitu\w*|pas(?:ar|a|alo|ame)|actualiz\w*)\s+(?:de\s+)?{_ARTICULO}(?P<a>{_CODIGO})\s+(?:por|a|con|x|->|=>)\s+{_ARTICULO}(?P<b>{_CODIGO})")
_INFO = re.compile(
    rf"\b(?:donde\s+(?:esta|se\s+usa|aparece|figura)|en\s+que\s+(?:carpetas?|planillas?|archivos?)\s+(?:esta|se\s+usa|aparece)|info\w*(?:\s+(?:de|sobre))?|que\s+es|descripcion\s+de|datos\s+de|mostr\w+|ver|detalle\s+de)\s+{_ARTICULO}(?P<c>{_CODIGO})")
_FALTA = re.compile(r"\b(?:avis\w+|falta\w*|no\s+existe\w*|no\s+hay\s+codigo|nuevo\s+codigo|dar\s+de\s+alta|alta\s+de)\b")
_PEDIDO_N = re.compile(r"\bpedido\s*(?:n[°ºo.]*\s*)?#?\s*(\d+)")
_PEDIDOS = re.compile(r"\b(?:mis\s+pedidos|pedidos|pendientes|por\s+aprobar)\b")
_PROHIBIDO = re.compile(r"\b(?:aprob(?:ar|a|ame|alo|ado)|aprueb(?:a|ame|alo)|rechaz(?:ar|a|alo)|revert(?:ir|i|ilo|ime)|deshac(?:er|e|elo)|"
                        r"limpi(?:ar|a|e|ame)|borr(?:ar|a|alo)|cancel(?:ar|a|alo)|ejecut(?:ar|a|alo)|elimin(?:ar|a|alo))\b")
_SALUDO = re.compile(r"^(?:hola|buenas|buenos|buen dia|ayuda|help|que podes|que puedes|que haces|que sabes|como te uso|como funciona|\?)")
_BUSCAR = re.compile(r"\b(?:busc\w+|necesit\w+|quier\w+|hay|existe\w*|tenemos|cual\s+es\s+el\s+codigo|codigos?\s+(?:de|del|para)|encontr\w+)\b")
_PARRAFO_FACTOR = (
    re.compile(r"\bpor\s+cada\s+(?:1|un|una|uno)\s+(?:\w+\s+){0,3}?(?:van|son|hay|equivale\w*|corresponde\w*)\s+(\d+(?:[.,]\d+)?)"),
    re.compile(r"\b(?:factor|multiplic\w*|cantidades?\s+x|cantidades?\s+por)\s*(?:de\s*)?x?\s*(\d+(?:[.,]\d+)?)"),
    re.compile(r"(?<![a-z0-9])x\s*(\d+(?:[.,]\d+)?)\b"),
)
_STOP = {"de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas", "para", "que", "codigo", "codigos", "algo", "alguno",
         "algun", "hay", "existe", "tenemos", "necesito", "quiero", "busco", "buscar", "busca", "con", "y", "en", "por", "se", "me",
         "mi", "mis", "lo", "al", "es", "cual", "como", "esta", "estan", "insumo", "material", "item", "producto"}
ESTADOS = {"pendiente": "espera aprobación", "ejecutando": "se está ejecutando", "completado": "completado", "parcial": "completado con pendientes",
           "rechazado": "rechazado", "cancelado": "cancelado", "revertido": "revertido", "falló": "falló"}


def _t(s):
    """Minúsculas, sin acentos y con espacios simples: para reconocer lo que se quiso decir."""
    s = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.lower()).strip()


def _r(text, bullets=None, codes=None, actions=None, links=None, suggest=None, error=False):
    out = {"text": text}
    for k, v in (("bullets", bullets), ("codes", codes), ("actions", actions), ("links", links), ("suggest", suggest)):
        if v:
            out[k] = v
    if error:
        out["error"] = True
    return out


def _codigo_ok(c):
    return c and c.upper() not in _NO_ES_CODIGO


def _titulo(o):
    p = o["params"]
    return f"Agregar reemplazos posibles de {p.get('code')}" if o["type"] == "reemplazos" else f"Cambiar {p.get('from')} por {p.get('to')}"


def _num(txt):
    return float(txt.replace(",", "."))


# --------------------------------------------------------------------------- interpretar
def interpretar(texto):
    """Devuelve (intención, datos). Intenciones: saludo, cambio, info, falta, pedido, pedidos, prohibido, buscar, nada."""
    orig = (texto or "").strip()
    t = _t(orig)
    if not t:
        return "nada", {}
    m = _CAMBIO.search(t)
    if m and _codigo_ok(m["a"]) and _codigo_ok(m["b"]):
        d = {"from": m["a"].upper(), "to": m["b"].upper(), "rev_mode": "ver" if re.search(r"version nueva|nueva version|\bv\s*\+\s*1\b|subir de version", t) else "rev"}
        for rx in _PARRAFO_FACTOR:
            f = rx.search(t[m.end():] if rx is _PARRAFO_FACTOR[2] else t)
            if f:
                d["factor"] = _num(f.group(1))
                break
        d["pide_cantidades"] = bool(re.search(r"cantidad", t))
        mm = re.search(r"\b(?:porque|motivo|razon)\s*:?\s*(.+)$", orig, re.I)
        d["reason"] = mm.group(1).strip() if mm else ""
        d["quiere_aprobar"] = bool(_PROHIBIDO.search(t))
        return "cambio", d
    if _PROHIBIDO.search(t):
        return "prohibido", {}
    if _SALUDO.match(t) and len(t.split()) <= 3:
        return "saludo", {}
    n = _PEDIDO_N.search(t)
    if n:
        return "pedido", {"id": int(n.group(1))}
    if _FALTA.search(t):
        return "falta", {"what": orig}
    if _PEDIDOS.search(t):
        return "pedidos", {"solo_pendientes": bool(re.search(r"pendiente|por aprobar", t)), "mios": bool(re.search(r"\bmis\b|\bmio", t))}
    i = _INFO.search(t)
    if i and _codigo_ok(i["c"]):
        return "info", {"code": i["c"].upper(), "resto": t[i.start("c"):]}
    return "buscar", {"texto": orig}


# --------------------------------------------------------------------------- respuestas
def _ayuda(user):
    puede = ["buscar códigos por nombre o descripción («busco disipador»)",
             "decirte en cuántas planillas y carpetas se usa un código («dónde está DIS206AA»)",
             "preparar un cambio de código: te muestro qué se modificaría y, si querés, lo mando a aprobación («cambiar DIS206AA por DIS206BA»)"]
    if can(user["role"], "request"):
        puede.append("avisar a Ingeniería que falta un código («avisar que falta el tornillo M8 inox»)")
    puede.append("contarte cómo van tus pedidos («mis pedidos», «pedido 12»)")
    return _r("Hola, soy el asistente del portal. Puedo ayudarte con esto:", bullets=puede + [
        "Lo que no hago: aprobar, rechazar ni revertir pedidos, ni ejecutar la limpieza. Eso lo decide siempre una persona desde Pedidos."],
        suggest=["cambiar CODIGO-ACTUAL por CODIGO-NUEVO", "dónde está CODIGO", "busco ", "mis pedidos"])


def _cambio(user, d):
    params = {"from": d["from"], "to": d["to"], "rev_mode": d["rev_mode"], "reason": d["reason"]}
    if d.get("factor"):
        params["qty"] = {"on": True, "factor": d["factor"]}
    try:
        pv = services.preview(user, params)
    except ValueError as e:
        similares = _buscar_descripcion(user, d["from"])[:5]
        return _r(str(e), codes=_chips(similares) if similares else None,
                  bullets=["¿Quisiste decir alguno de estos códigos?"] if similares else None, error=True)
    s, p = pv["sim"], pv["params"]
    cel, arc, car = s["total_cells"], s["total_files"], len(s["folders"])
    lineas = [f"Se cambia {p['from']} por {p['to']} en {services._pl(cel, 'celda')} de {services._pl(arc, 'archivo')} en {services._pl(car, 'carpeta')}.",
              ("Cada archivo pasa a una versión nueva (V+1) y la anterior sigue vigente." if p["rev_mode"] == "ver"
               else "Cada archivo pasa a la revisión siguiente (R+1) y la anterior va a OBSOLETO.")]
    if p["qty"]["on"]:
        lineas.append(f"Cambian las cantidades: por cada 1 de {p['from']} van {p['qty']['factor']:g} de {p['to']}.")
    elif d.get("pide_cantidades"):
        lineas.append("Mencionaste cantidades, pero no entendí el factor. Decime por ejemplo «cantidades x2» o cargalo en el formulario.")
    elif pv.get("conv"):
        lineas.append(f"Ya hay una conversión guardada para este cambio (por cada 1 van {pv['conv']['factor']:g}); no la apliqué. "
                      "Si corresponde, decime «con cantidades x" + f"{pv['conv']['factor']:g}" + "».")
    lineas += pv["warnings"]
    lineas.append("Nada se modifica hasta que lo apruebe otra persona.")
    if d.get("quiere_aprobar"):
        lineas.append("Yo no apruebo pedidos: eso lo hace Ingeniería o Gerencia desde Pedidos.")
    acciones, enlaces = [], [{"label": "Abrir en el formulario completo", "href": "#/nuevo?from=" + p["from"]}]
    if can(user["role"], "create"):
        acciones.append({"label": "Enviar a aprobación", "do": "create_order", "params": p, "style": "primary"})
    return _r("Esto es lo que va a pasar:", bullets=lineas, actions=acciones, links=enlaces)


def _chips(filas):
    return [{"code": f["code"], "desc": f["desc"], "cells": f.get("cells", 0), "folders": f.get("folders", 0)} for f in filas]


def _buscar_descripcion(user, texto):
    palabras = [w for w in re.findall(r"[a-z0-9][a-z0-9._/\-]*", _t(texto)) if w not in _STOP and len(w) > 1]
    if not palabras:
        return []
    out = []
    for f in services.catalog_list(user, "", limit=1_000_000):
        h = _t(f["code"] + " " + f["desc"])
        if all(w in h for w in palabras):
            out.append(f)
    exacto = " ".join(palabras).upper()
    out.sort(key=lambda f: (f["code"] != exacto, -f["cells"], f["code"]))
    return out


def _info(user, d):
    try:
        c = services.code_info(user, d["code"])
    except LookupError:
        return _buscar(user, {"texto": d["resto"]})
    lineas = [("Aparece en " + services._pl(c["cells"], "celda") + " de " + services._pl(c["folders"], "carpeta") + ".") if c["cells"] else "No aparece en ninguna planilla."]
    if c["alts"]:
        lineas.append("Reemplazos posibles: " + ", ".join(c["alts"]) + ".")
    desc = c["desc"] or "sin descripción cargada"
    return _r(f"{c['code']}: {desc}.", bullets=lineas, codes=_chips([c]),
              links=[{"label": "Cambiar este código", "href": "#/nuevo?from=" + c["code"]}])


def _buscar(user, d):
    texto = d["texto"]
    filas = _buscar_descripcion(user, texto)
    if filas:
        n = len(filas)
        return _r(f"Encontré {services._pl(n, 'código')}" + (f". Te muestro los primeros {8}:" if n > 8 else ":"), codes=_chips(filas[:8]),
                  bullets=["Tocá un código para ver dónde se usa."])
    acciones = []
    msg = "No encontré códigos que coincidan. Probá con menos palabras o con parte del código."
    if can(user["role"], "request") and len(_t(texto)) >= 5:
        msg += " Si el código todavía no existe, puedo avisarle a Ingeniería."
        acciones.append({"label": "Avisar que falta", "do": "request_code", "params": {"what": texto.strip()}, "style": "primary"})
    return _r(msg, actions=acciones)


def _falta(user, d):
    what = re.sub(r"^\s*(?:por favor,?\s*)?(?:quiero\s+|necesito\s+|podes\s+|podés\s+)?avis\w+\s+(?:que\s+)?", "", d["what"], flags=re.I).strip() or d["what"]
    if not can(user["role"], "request"):
        return _r("Los avisos de códigos que faltan los hace Abastecimiento. Como Ingeniería, podés generar el código directamente desde Códigos.",
                  links=[{"label": "Ir a Códigos que faltan", "href": "#/solicitudes"}])
    if len(what) < 5:
        return _r("Contame qué código o insumo falta (por ejemplo: «falta el tornillo M8 inox»).")
    similares = _buscar_descripcion(user, what)[:5]
    return _r(f"Voy a avisarle a Ingeniería: «{what}».",
              bullets=["Ya hay códigos parecidos. Fijate antes de avisar:"] if similares else None, codes=_chips(similares) if similares else None,
              actions=[{"label": "Enviar el aviso", "do": "request_code", "params": {"what": what}, "style": "primary"}])


def _resumen_pedido(o):
    s = o.get("summary")
    det = f" ({services._pl(s['cells'], 'celda')} en {services._pl(s['folders'], 'carpeta')})" if s else ""
    return f"#{o['id']} · {_titulo(o)} · {ESTADOS.get(o['status'], o['status'])}{det}"


def _pedido(user, d):
    try:
        o = services.get_order(user, d["id"])
    except LookupError:
        return _r(f"No existe el pedido #{d['id']}.", error=True)
    lineas = [f"Lo pidió {o['created_by']} el {o['created_at'][:10]}."]
    if o.get("decided_by"):
        lineas.append(f"Lo resolvió {o['decided_by']}.")
    if o["status"] == "pendiente":
        lineas.append("Espera que lo apruebe una persona de Ingeniería o Gerencia (quien lo pidió no puede aprobarlo).")
    return _r(_resumen_pedido(o), bullets=lineas, links=[{"label": f"Abrir el pedido #{o['id']}", "href": f"#/pedidos/{o['id']}"}])


def _pedidos(user, d):
    if d["solo_pendientes"] and can(user["role"], "approve") and not d["mios"]:
        # quien aprueba: los pendientes de otras personas (Sistemas puede aprobar los propios)
        lista = [o for o in services.list_orders(user, "pendiente") if o["created_by"] != user["email"] or user["role"] == "Sistemas"]
        titulo, vacio = "Pedidos que esperan tu aprobación", "No hay pedidos esperando tu aprobación."
    else:
        lista = [o for o in services.list_orders(user, "pendiente" if d["solo_pendientes"] else None) if o["created_by"] == user["email"]]
        titulo, vacio = ("Tus pedidos pendientes", "No tenés pedidos pendientes.") if d["solo_pendientes"] else ("Tus últimos pedidos", "Todavía no hiciste pedidos.")
    if not lista:
        return _r(vacio, links=[{"label": "Ir a Pedidos", "href": "#/pedidos"}])
    return _r(f"{titulo} ({min(len(lista), 5)} de {len(lista)}):", bullets=[_resumen_pedido(o) for o in lista[:5]],
              links=[{"label": "Ver todos los pedidos", "href": "#/pedidos"}])


def responder(user, texto):
    texto = (texto or "").strip()[:MAX_TEXTO]
    if not texto:
        raise ValueError("Escribí tu consulta.")
    intencion, d = interpretar(texto)
    if intencion == "saludo" or intencion == "nada":
        return _ayuda(user)
    if intencion == "cambio":
        return _cambio(user, d)
    if intencion == "prohibido":
        return _r("Eso no lo hago yo: aprobar, rechazar, cancelar, revertir o ejecutar la limpieza lo decide siempre una persona, desde Pedidos. "
                  "Yo puedo dejarte el pedido listo y avisarte cómo va.", links=[{"label": "Ir a Pedidos", "href": "#/pedidos"}])
    if intencion == "pedido":
        return _pedido(user, d)
    if intencion == "pedidos":
        return _pedidos(user, d)
    if intencion == "falta":
        return _falta(user, d)
    if intencion == "info":
        return _info(user, d)
    return _buscar(user, d)


def ejecutar(user, accion, params):
    """Acciones que la persona confirmó con un botón. Se validan con las mismas reglas y permisos que el formulario."""
    if accion not in ACCIONES:
        raise ValueError("El asistente no puede hacer esa acción.")
    params = params or {}
    if accion == "create_order":
        o = services.create_order(user, "codigo", params)
        return _r(f"Listo: el pedido #{o['id']} quedó enviado a aprobación ({o['params']['from']} → {o['params']['to']}).",
                  bullets=["Lo revisa y aprueba una persona de Ingeniería o Gerencia; yo no puedo aprobarlo."],
                  links=[{"label": f"Abrir el pedido #{o['id']}", "href": f"#/pedidos/{o['id']}"}])
    r = services.request_code(user, params.get("what", ""), params.get("kind", ""))
    return _r("Listo: le avisé a Ingeniería que falta ese código.", links=[{"label": "Ver los avisos", "href": "#/solicitudes"}]) if r else _r("No se pudo enviar el aviso.", error=True)


def handle(user, body):
    """Punto de entrada de la API: {"message": "..."} o {"do": "create_order", "params": {...}}."""
    require(user, "view")
    if body.get("do"):
        return ejecutar(user, body["do"], body.get("params"))
    return responder(user, body.get("message", ""))
