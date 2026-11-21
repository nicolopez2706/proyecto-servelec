"""Avisos del portal hacia n8n (o cualquier sistema que reciba un webhook).

Cada accion importante (pedido creado, aprobado, ejecutado, revertido, codigo que falta...) se envia como un POST con JSON a
N8N_WEBHOOK_URL. Se hace en segundo plano y nunca frena ni rompe lo que esta haciendo el portal: si n8n no responde, se
reintenta unas veces y se avisa en la consola. Si N8N_WEBHOOK_URL esta vacio, no se envia nada."""
import json
import queue
import threading
import time
import urllib.error
import urllib.request

from .config import settings

# accion registrada en la auditoria -> nombre del evento que recibe n8n
KINDS = {
    "Creó pedido": "pedido_creado",
    "Creó pedido de reemplazos": "pedido_creado",
    "Aprobó pedido": "pedido_aprobado",
    "Aprobó pedido de reemplazos": "pedido_completado",
    "Ejecutó pedido": "pedido_ejecutado",
    "Rechazó pedido": "pedido_rechazado",
    "Canceló pedido": "pedido_cancelado",
    "Revirtió pedido": "pedido_revertido",
    "Recuperó pedido interrumpido": "pedido_interrumpido",
    "Avisó que falta un código": "codigo_faltante",
    "Generó código": "codigo_generado",
    "Rechazó solicitud de código": "solicitud_rechazada",
    "Limpieza de obsoletos": "limpieza_obsoletos",
}

_Q = queue.Queue(maxsize=500)
_LOCK = threading.Lock()
_THREAD = None
ATTEMPTS = (0, 2, 6)             # segundos de espera antes de cada intento


def _order_summary(target):
    """Resumen del pedido si el aviso es sobre uno ('#7')."""
    if not (isinstance(target, str) and target.startswith("#") and target[1:].isdigit()):
        return None
    from .db import connect
    con = connect()
    try:
        r = con.execute("SELECT * FROM orders WHERE id=?", (int(target[1:]),)).fetchone()
        if not r:
            return None
        params = json.loads(r["params"] or "{}")
        sim = json.loads(r["sim"]) if r["sim"] else None
        res = json.loads(r["result"]) if r["result"] else None
        names = {x["email"]: x["name"] for x in con.execute("SELECT email,name FROM users")}
        out = {"id": r["id"], "type": r["type"], "status": r["status"], "reason": params.get("reason", ""),
               "requested_by": names.get(r["created_by"], r["created_by"]),
               "decided_by": names.get(r["decided_by"], r["decided_by"] or ""), "decided_at": r["decided_at"],
               "created_at": r["created_at"], "url": f"{settings.portal_url}/#/pedidos/{r['id']}"}
        if r["type"] == "codigo":
            out["from"], out["to"] = params.get("from", ""), params.get("to", "")
        else:
            out["code"], out["alts"] = params.get("code", ""), params.get("alts", [])
        if sim:
            out["planned"] = {"cells": sim.get("total_cells", 0), "files": sim.get("total_files", 0), "folders": len(sim.get("folders", {}))}
        if res:
            out["result"] = {"changes": res.get("changes", 0), "files": len(res.get("done", [])),
                             "failed": len(res.get("failed", [])), "skipped": len(res.get("skipped", []))}
        return out
    finally:
        con.close()


def _payload(item):
    p = {"event": item["kind"], "action": item["action"], "target": item["target"], "detail": item["detail"],
         "by": item["by"], "at": item["at"], "portal_url": settings.portal_url}
    try:
        o = _order_summary(item["target"])
    except Exception:
        o = None
    if o:
        p["order"] = o
    return p


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post(payload):
    """Un envio. Devuelve (ok, estado_http_o_None, texto_de_error)."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(settings.n8n_url, data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8", "X-Portal-Event": payload["event"]})
    if settings.n8n_secret:
        req.add_header("X-Portal-Token", settings.n8n_secret)
    try:
        # sin proxy: n8n corre en la misma red/equipo; un proxy de Windows configurado para internet lo rompería
        with _OPENER.open(req, timeout=8) as r:
            return 200 <= r.status < 300, r.status, ""
    except urllib.error.HTTPError as e:
        return False, e.code, f"n8n respondió {e.code}" + (" (¿el flujo está activo? en modo de prueba solo recibe mientras se escucha)" if e.code == 404 else "")
    except Exception as e:
        return False, None, f"{type(e).__name__}: {e}"


def _worker():
    while True:
        item = _Q.get()
        try:
            wait = item["ts"] + settings.n8n_delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)                 # deja que la operacion termine de guardarse antes de avisar
            payload, last = _payload(item), ""
            for pause in ATTEMPTS:
                if pause:
                    time.sleep(pause)
                ok, _, last = _post(payload)
                if ok:
                    break
            else:
                print(f"[portal] no se pudo avisar a n8n ({payload['event']}): {last}", flush=True)
        except Exception as e:
            print(f"[portal] error al avisar a n8n: {e!r}", flush=True)


def emit(action, target="", detail="", by=""):
    """Encola un aviso. Nunca lanza errores ni espera."""
    try:
        kind = KINDS.get(action)
        if not kind or not settings.n8n_url:
            return False
        global _THREAD
        with _LOCK:
            if _THREAD is None or not _THREAD.is_alive():
                _THREAD = threading.Thread(target=_worker, daemon=True, name="avisos-n8n")
                _THREAD.start()
        _Q.put_nowait({"kind": kind, "action": action, "target": target or "", "detail": detail or "", "by": by or "",
                       "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "ts": time.monotonic()})
        return True
    except Exception:
        return False


def send_test(by=""):
    """Envia un aviso de prueba ahora y dice si n8n lo recibio (para el boton de 'Estado del sistema')."""
    if not settings.n8n_url:
        return {"ok": False, "configured": False, "error": "Falta N8N_WEBHOOK_URL en el archivo .env."}
    ok, status, err = _post({"event": "prueba", "action": "Aviso de prueba", "target": "", "detail": "Si lo ves en n8n, la conexión funciona.",
                             "by": by, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "portal_url": settings.portal_url})
    return {"ok": ok, "configured": True, "status": status, "error": err}
