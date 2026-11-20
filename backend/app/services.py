"""Logica de negocio: usuarios, pedidos, aprobacion con seleccion de carpetas, ejecucion,
reversion y auditoria. No depende de FastAPI (se prueba sola)."""
import datetime as dt
import json
from pathlib import Path

from . import engine, events
from .config import settings
from .db import connect
from .permissions import PermissionDenied, can, require, ROLES
from .security import check_password, hash_password

OPEN = "pendiente"


def _writable():
    if settings.read_only:
        raise ValueError("El portal está en modo solo lectura (READ_ONLY=1 en .env): se puede simular y pedir cambios, "
                         "pero no aplicarlos ni revertirlos.")


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


# ---------- auditoria / eventos ----------
def audit(con, by, action, target="", detail=""):
    con.execute("INSERT INTO audit(at,by,action,target,detail) VALUES(?,?,?,?,?)", (now(), by, action, target, detail))
    events.emit(action, target, detail, by)         # aviso a n8n (en segundo plano, si esta configurado)


def event(con, order_id, by, text):
    con.execute("INSERT INTO events(order_id,at,by,text) VALUES(?,?,?,?)", (order_id, now(), by, text))


# ---------- usuarios ----------
def user_public(r):
    return {"email": r["email"], "name": r["name"], "role": r["role"], "active": bool(r["active"])}


def authenticate(email, password):
    con = connect()
    try:
        r = con.execute("SELECT * FROM users WHERE email=? AND active=1", (email.strip().lower(),)).fetchone()
        if r and check_password(password, r["pw_hash"]):
            audit(con, r["email"], "Ingresó")
            con.commit()
            return user_public(r)
        return None
    finally:
        con.close()


def get_user(email):
    con = connect()
    try:
        r = con.execute("SELECT * FROM users WHERE email=? AND active=1", (email,)).fetchone()
        return user_public(r) if r else None
    finally:
        con.close()


def change_password(user, current, new):
    """Cada persona cambia su propia clave. Pide la clave actual para que nadie la cambie desde una sesion abierta ajena."""
    if not current or not new:
        raise ValueError("Completá la clave actual y la nueva.")
    if len(new) < 8:
        raise ValueError("La clave nueva debe tener al menos 8 caracteres.")
    if new == current:
        raise ValueError("La clave nueva tiene que ser distinta de la actual.")
    con = connect()
    try:
        r = con.execute("SELECT * FROM users WHERE email=? AND active=1", (user["email"],)).fetchone()
        if not r or not check_password(current, r["pw_hash"]):
            raise ValueError("La clave actual no es correcta.")
        con.execute("UPDATE users SET pw_hash=? WHERE email=?", (hash_password(new), user["email"]))
        audit(con, user["email"], "Cambió su clave")
        con.commit()
        return {"ok": True}
    finally:
        con.close()


def create_user(actor, email, name, role, password):
    require(actor, "admin")
    if role not in ROLES:
        raise ValueError("Rol inválido.")
    if len(password) < 8:
        raise ValueError("La clave debe tener al menos 8 caracteres.")
    con = connect()
    try:
        con.execute("INSERT INTO users(email,name,role,pw_hash) VALUES(?,?,?,?)",
                    (email.strip().lower(), name.strip(), role, hash_password(password)))
        audit(con, actor["email"], "Creó usuario", email, role)
        con.commit()
    except Exception as e:
        if "UNIQUE" in str(e):
            raise ValueError("Ese correo ya existe.")
        raise
    finally:
        con.close()


def list_users(actor):
    require(actor, "admin")
    con = connect()
    try:
        return [user_public(r) for r in con.execute("SELECT * FROM users ORDER BY name")]
    finally:
        con.close()


def set_role(actor, email, role=None, active=None):
    require(actor, "admin")
    con = connect()
    try:
        if role is not None:
            if role not in ROLES:
                raise ValueError("Rol inválido.")
            con.execute("UPDATE users SET role=? WHERE email=?", (role, email))
            audit(con, actor["email"], "Cambió el rol", email, role)
        if active is not None:
            con.execute("UPDATE users SET active=? WHERE email=?", (1 if active else 0, email))
            audit(con, actor["email"], "Activó/desactivó usuario", email, str(active))
        con.commit()
    finally:
        con.close()


# ---------- pedidos ----------
def _order(con, oid):
    r = con.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not r:
        raise LookupError("No existe el pedido.")
    return r


def order_dict(con, r, detail=True):
    d = {"id": r["id"], "type": r["type"], "status": r["status"], "params": json.loads(r["params"]),
         "created_by": r["created_by"], "created_at": r["created_at"],
         "decided_by": r["decided_by"], "decided_at": r["decided_at"]}
    sim = json.loads(r["sim"]) if r["sim"] else None
    d["summary"] = ({"cells": sim["total_cells"], "files": sim["total_files"], "folders": len(sim["folders"]),
                     "skipped": len(sim["skipped"])} if sim else None)
    if detail:
        d["sim"] = sim
        d["result"] = json.loads(r["result"]) if r["result"] else None
        d["events"] = [dict(e) for e in con.execute("SELECT at,by,text FROM events WHERE order_id=? ORDER BY id", (r["id"],))]
    return d


def _clean_codigo(params):
    old, new = engine.norm(params.get("from")), engine.norm(params.get("to"))
    if not old or not new:
        raise ValueError("Falta el código actual o el nuevo.")
    if old == new:
        raise ValueError("El código nuevo es igual al actual.")
    mode = params.get("rev_mode", "rev")
    if mode not in ("rev", "ver"):
        raise ValueError("rev_mode debe ser 'rev' o 'ver'.")
    qty = params.get("qty") or {}
    factor = float(qty["factor"]) if qty.get("on") and qty.get("factor") not in (None, "") else None
    if factor is not None and factor <= 0:
        raise ValueError("El factor de cantidad debe ser mayor que cero.")
    alts = _clean_alts(params.get("alts"), new)
    return {"from": old, "to": new, "rev_mode": mode, "suggest_col": _flag(params.get("suggest_col")) and bool(alts),
            "reason": (params.get("reason") or "").strip(),
            "qty": {"on": factor is not None, "factor": factor, "note": (qty.get("note") or "").strip(),
                    "save": bool(qty.get("save", True))},
            "alts": alts}


def _flag(v):
    return v is True or str(v).lower() in ("1", "true", "on", "si", "sí")


def _clean_alts(alts, own):
    if isinstance(alts, str):
        alts = [a for a in alts.replace(";", ",").split(",")]
    out = []
    for a in alts or []:
        a = engine.norm(a)
        if a and a != own and a not in out:
            out.append(a)
    return out


def _pl(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def _not_found(code, sim):
    msg = f"No se encontró {code} en las planillas que se pudieron leer."
    extra = engine.explain_skipped(sim.get("skipped"))
    return msg + (" " + extra if extra else "")


def preview(user, params):
    """Simulacion sin guardar nada: para mostrar 'qué se va a cambiar' antes de enviar."""
    require(user, "create")
    clean = _clean_codigo(params)
    sim = engine.simulate(settings.docs_root, clean["from"])
    if not sim["total_cells"]:
        raise ValueError(_not_found(clean["from"], sim))
    dest = engine.simulate(settings.docs_root, clean["to"])
    warnings = []
    if dest["total_cells"]:
        warnings.append(f"{clean['to']} ya se usa en {_pl(dest['total_cells'], 'celda')}. Está bien si querés unificar, pero revisalo.")
    both = set(f["file"] for fs in sim["folders"].values() for f in fs) & set(f["file"] for fs in dest["folders"].values() for f in fs)
    if both:
        warnings.append(f"{_pl(len(both), 'archivo')} ya {'usa' if len(both) == 1 else 'usan'} los dos códigos. Después del cambio puede haber líneas duplicadas.")
    if sim.get("drive_files"):
        warnings.append(f"{_pl(sim['drive_files'], 'archivo')} {'tiene' if sim['drive_files'] == 1 else 'tienen'} su planilla gemela en Google Sheets. "
                        "Esas copias todavía NO se modifican desde el portal: van a quedar con el código anterior hasta que se conecte Google.")
    if sim["skipped"]:
        warnings.append(f"{_pl(len(sim['skipped']), 'archivo')} se {'va' if len(sim['skipped']) == 1 else 'van'} a omitir y {'queda' if len(sim['skipped']) == 1 else 'quedan'} pendiente(s) (abierto, formato o error).")
    conv = get_conv(user, clean["from"], clean["to"])
    return {"params": clean, "sim": sim, "warnings": warnings, "conv": conv}


def create_order(user, typ, params):
    require(user, "create")
    if typ == "reemplazos":
        return _create_reemplazos(user, params)
    if typ != "codigo":
        raise ValueError("Tipo de pedido no soportado.")
    clean = _clean_codigo(params)
    sim = engine.simulate(settings.docs_root, clean["from"])
    if not sim["total_cells"]:
        raise ValueError(_not_found(clean["from"], sim))
    con = connect()
    try:
        cur = con.execute("INSERT INTO orders(type,status,params,sim,created_by,created_at) VALUES(?,?,?,?,?,?)",
                          ("codigo", OPEN, json.dumps(clean), json.dumps(sim), user["email"], now()))
        oid = cur.lastrowid
        event(con, oid, user["email"], f"Creó el pedido. La simulación quedó guardada ({sim['total_cells']} celdas en "
                                       f"{sim['total_files']} archivos de {len(sim['folders'])} carpetas).")
        audit(con, user["email"], "Creó pedido", f"#{oid}", f"{clean['from']} → {clean['to']}")
        con.commit()
        return order_dict(con, _order(con, oid))
    finally:
        con.close()


def _create_reemplazos(user, params):
    code = engine.norm(params.get("code"))
    alts = _clean_alts(params.get("alts"), code)
    if not code:
        raise ValueError("Falta el código.")
    if not alts:
        raise ValueError("Cargá al menos un código posible.")
    known = code_exists(code)
    if not known:
        raise ValueError(f"{code} no existe en el catálogo ni en las planillas.")
    mode = params.get("rev_mode", "rev")
    if mode not in ("rev", "ver"):
        raise ValueError("rev_mode debe ser 'rev' o 'ver'.")
    clean = {"code": code, "alts": alts, "reason": (params.get("reason") or "").strip(),
             "planillas": _flag(params.get("planillas")), "rev_mode": mode}
    sim = None
    if clean["planillas"]:
        sim = engine.simulate(settings.docs_root, code)
        if not sim["total_cells"]:
            raise ValueError(f"{code} no aparece en ninguna planilla. Destildá «escribir en las planillas» para cargarlo solo en el catálogo.")
    con = connect()
    try:
        cur = con.execute("INSERT INTO orders(type,status,params,sim,created_by,created_at) VALUES(?,?,?,?,?,?)",
                          ("reemplazos", OPEN, json.dumps(clean), json.dumps(sim) if sim else None, user["email"], now()))
        oid = cur.lastrowid
        tail = (f"También se escribe la columna «Sugerencias» en {sim['total_files']} archivos de {len(sim['folders'])} carpetas."
                if sim else "No se modifican planillas.")
        event(con, oid, user["email"], f"Creó el pedido: agregar {', '.join(alts)} como reemplazos posibles de {code}. {tail}")
        audit(con, user["email"], "Creó pedido de reemplazos", f"#{oid}", f"{code}: {', '.join(alts)}")
        con.commit()
        return order_dict(con, _order(con, oid))
    finally:
        con.close()


def list_orders(user, status=None):
    require(user, "view")
    con = connect()
    try:
        q, a = "SELECT * FROM orders", []
        if status:
            q += " WHERE status=?"; a.append(status)
        return [order_dict(con, r, detail=False) for r in con.execute(q + " ORDER BY id DESC", a)]
    finally:
        con.close()


def get_order(user, oid):
    require(user, "view")
    con = connect()
    try:
        return order_dict(con, _order(con, oid))
    finally:
        con.close()


def reject(user, oid, comment=""):
    require(user, "approve")
    con = connect()
    try:
        o = _order(con, oid)
        if o["status"] != OPEN:
            raise ValueError("El pedido ya no está esperando decisión.")
        con.execute("UPDATE orders SET status='rechazado', decided_by=?, decided_at=? WHERE id=?", (user["email"], now(), oid))
        event(con, oid, user["email"], "Rechazó el pedido." + (f" Motivo: {comment}" if comment else ""))
        audit(con, user["email"], "Rechazó pedido", f"#{oid}", comment)
        con.commit()
        return order_dict(con, _order(con, oid))
    finally:
        con.close()


def cancel(user, oid):
    con = connect()
    try:
        o = _order(con, oid)
        if o["status"] != OPEN:
            raise ValueError("Solo se cancelan pedidos pendientes.")
        if o["created_by"] != user["email"] and user["role"] != "Sistemas":
            raise PermissionDenied("Solo quien lo pidió (o Sistemas) puede cancelarlo.")
        con.execute("UPDATE orders SET status='cancelado' WHERE id=?", (oid,))
        event(con, oid, user["email"], "Canceló el pedido.")
        audit(con, user["email"], "Canceló pedido", f"#{oid}")
        con.commit()
        return order_dict(con, _order(con, oid))
    finally:
        con.close()


def approve(user, oid, comment="", folders=None):
    """Aprueba y ejecuta. 'folders' = carpetas elegidas (subconjunto de las que tienen el código).
    Sin 'folders' se aplican todas. Elegir carpetas requiere el permiso 'pickfolders'."""
    require(user, "approve")
    _writable()
    con = connect()
    try:
        o = _order(con, oid)
        if o["status"] != OPEN:
            raise ValueError("El pedido ya no está esperando decisión.")
        if o["created_by"] == user["email"] and user["role"] != "Sistemas":
            raise PermissionDenied("No podés aprobar un pedido que pediste vos.")
        if o["type"] == "reemplazos" and not json.loads(o["params"]).get("planillas"):
            return _approve_reemplazos(con, o, user, comment)
        params, sim = json.loads(o["params"]), json.loads(o["sim"])
        cand = list(sim["folders"].keys())
        chosen = cand
        if folders is not None:
            require(user, "pickfolders")
            bad = [f for f in folders if f not in cand]
            if bad:
                raise ValueError("Carpetas que no tienen el código: " + ", ".join(bad))
            if not folders:
                raise ValueError("Elegí al menos una carpeta.")
            chosen = [f for f in cand if f in folders]
            if len(chosen) != len(cand):
                params["scope_orig"] = cand
                event(con, oid, user["email"], f"Cambió las carpetas donde se aplica: queda en {len(chosen)} de {len(cand)}, "
                                               f"sacó {len(cand) - len(chosen)}.")
        params["scope"] = chosen
        con.execute("UPDATE orders SET params=?, status='ejecutando', decided_by=?, decided_at=? WHERE id=?",
                    (json.dumps(params), user["email"], now(), oid))
        event(con, oid, user["email"], "Aprobó el pedido." + (f" Comentario: {comment}" if comment else ""))
        audit(con, user["email"], "Aprobó pedido", f"#{oid}", f"{len(chosen)} carpetas")
        con.commit()
        try:
            _execute(con, oid, user)
        except Exception as e:                      # que no quede "ejecutando" para siempre si algo falla
            res = {"done": [], "failed": [{"file": "(todo el pedido)", "why": f"error inesperado: {e}"}], "skipped": [],
                   "changes": 0, "unchanged": [], "links_updated": 0, "links_manual": []}
            con.execute("UPDATE orders SET status='falló', result=? WHERE id=?", (json.dumps(res), oid))
            event(con, oid, "sistema", f"La ejecución se cortó por un error inesperado: {e}. Revisá los respaldos antes de repetir.")
            con.commit()
            raise
        return order_dict(con, _order(con, oid))
    finally:
        con.close()


def recover_interrupted():
    """Al arrancar: pedidos que quedaron 'ejecutando' porque el portal se cerro a mitad de la ejecucion.
    Sin respaldos todavia no se toco ninguna planilla: vuelven a quedar pendientes. Con respaldos puede haber
    cambios a medias: quedan como 'falló' para revisarlos a mano con la carpeta de respaldos."""
    con = connect()
    try:
        rows = con.execute("SELECT id FROM orders WHERE status='ejecutando'").fetchall()
        for r in rows:
            oid = r["id"]
            bdir = settings.backup_dir / f"pedido_{oid}"
            tocado = bdir.exists() and any(f.is_file() for f in bdir.rglob("*"))
            if not tocado:
                con.execute("UPDATE orders SET status=? WHERE id=?", (OPEN, oid))
                event(con, oid, "sistema", "El portal se cerró mientras ejecutaba este pedido, antes de modificar ninguna planilla. "
                                           "Vuelve a quedar pendiente para aprobarlo de nuevo.")
            else:
                res = {"done": [], "failed": [{"file": "(todo el pedido)", "why": "el portal se cerró a mitad de la ejecución"}],
                       "skipped": [], "changes": 0, "unchanged": [], "links_updated": 0, "links_manual": []}
                con.execute("UPDATE orders SET status='falló', result=? WHERE id=?", (json.dumps(res), oid))
                event(con, oid, "sistema", f"El portal se cerró a mitad de la ejecución y puede haber planillas cambiadas. "
                                           f"Los respaldos están en {bdir}. Revisá antes de repetir el pedido.")
            audit(con, "sistema", "Recuperó pedido interrumpido", f"#{oid}", "pendiente" if not tocado else "falló")
        con.commit()
        return [r["id"] for r in rows]
    finally:
        con.close()


def _execute(con, oid, approver):
    o = _order(con, oid)
    params = json.loads(o["params"])
    root, bdir = settings.docs_root, settings.backup_dir / f"pedido_{oid}"
    # volver a simular: los archivos pudieron cambiar desde que se pidió
    code_src = params["from"] if "from" in params else params["code"]
    fresh = engine.simulate(root, code_src)
    todo = [f for fol in params["scope"] for f in fresh["folders"].get(fol, [])]
    if fresh["total_cells"] != json.loads(o["sim"])["total_cells"]:
        event(con, oid, "sistema", f"Se volvió a simular y cambió el resultado: {fresh['total_cells']} celdas "
                                   f"(antes {json.loads(o['sim'])['total_cells']}). Se usa la simulación nueva.")
    done, failed, changes_n, unchanged = [], [], 0, []
    qty = params.get("qty") or {"on": False}
    factor = qty["factor"] if qty["on"] else None
    creator = get_user(o["created_by"]) or {"name": o["created_by"]}
    for f in todo:
        path = engine.from_rel(root, f["file"])
        try:
            bk = engine.backup_file(root, path, bdir)
            info = {"bk": bk}
            obs_prev = None
            if params["rev_mode"] == "rev":
                op = path.parent / engine.OBS / path.name
                if op.exists():
                    obs_prev = engine.backup_file(root, op, bdir / "_obs_previos")
            doc = engine.open_doc(path)
            if "from" in params:
                changes = doc.apply_change(params["from"], params["to"], factor)
                what = f"{params['from']} por {params['to']} en {len(changes)} celdas"
                if factor:
                    what += f"; cantidades x{factor:g}"
                if params.get("suggest_col") and params.get("alts"):
                    sug = doc.add_suggestions(params["to"], params["alts"])
                    changes += sug
                    what += f"; sugerencias ({', '.join(params['alts'])}) en {len(sug)} filas"
            else:                                           # pedido de reemplazos posibles escrito en planillas
                changes = doc.add_suggestions(params["code"], params["alts"])
                what = f"Sugerencias de {params['code']} ({', '.join(params['alts'])}) en {len(changes)} filas"
                if not changes:
                    unchanged.append(f["file"])
                    continue
            entry = {"what": what, "why": params["reason"], "by": creator["name"], "auth": approver["name"],
                     "date": dt.date.today().strftime("%d/%m/%Y"), "order": f"#{oid}"}
            res = engine.revise_and_save(path, doc, params["rev_mode"], entry)
            info.update(res, obs_prev=obs_prev, cells=len(changes), file=f["file"])
            if f.get("via"):                                # accesos directos que apuntaban a este archivo
                if params["rev_mode"] == "rev":
                    info["links"], man = engine.retarget_links(root, f["via"], path, res["new"], bdir)
                    info["links_manual"] = man
                else:
                    info["links_vigentes"] = [v["lnk"] for v in f["via"]]   # la version anterior sigue vigente
            done.append(info)
            changes_n += len(changes)
        except Exception as e:
            failed.append({"file": f["file"], "why": str(e)})
    if not done and unchanged and not failed:
        status = "completado"          # las planillas ya tenian esas sugerencias: solo se cargo el catalogo
    elif not done:
        status = "falló"
    elif failed or fresh["skipped"]:
        status = "parcial"          # quedaron archivos pendientes (abiertos, .ods, con error)
    else:
        status = "completado"
    result = {"done": done, "failed": failed, "skipped": fresh["skipped"], "changes": changes_n, "unchanged": unchanged,
              "links_updated": sum(len(d.get("links", [])) for d in done),
              "links_manual": [m for d in done for m in d.get("links_manual", [])]}
    if done:
        engine.reset_cache()
        if params.get("alts"):
            result["alts_added"] = _add_alts(con, params.get("to") or params["code"], params["alts"], o["created_by"])
            result["added"] = result["alts_added"]
        if qty["on"] and qty.get("save", True):
            key = f"{params['from']}>{params['to']}"
            con.execute("INSERT OR REPLACE INTO conv(key,factor,note,by,at) VALUES(?,?,?,?,?)",
                        (key, qty["factor"], qty["note"], o["created_by"], now()))
            result["conv_saved"] = key
    con.execute("UPDATE orders SET status=?, result=? WHERE id=?", (status, json.dumps(result), oid))
    event(con, oid, "sistema", f"Terminó: {changes_n} cambios en {len(done)} archivos"
                                + (f", {len(failed)} con error" if failed else "")
                                + (f", {len(fresh['skipped'])} omitidos" if fresh["skipped"] else "")
                                + (f"; {result['links_updated']} accesos directos actualizados" if result["links_updated"] else "")
                                + (f"; {len(result['links_manual'])} accesos directos para revisar a mano" if result["links_manual"] else "") + ".")
    audit(con, "sistema", "Ejecutó pedido", f"#{oid}", status)
    con.commit()


def revert(user, oid):
    require(user, "revert")
    _writable()
    con = connect()
    try:
        o = _order(con, oid)
        if o["status"] not in ("completado", "parcial"):
            raise ValueError("Solo se revierten pedidos ejecutados.")
        res = json.loads(o["result"])
        if o["type"] == "reemplazos" and "done" not in res:
            _remove_alts(con, json.loads(o["params"])["code"], res["added"])
            con.execute("UPDATE orders SET status='revertido' WHERE id=?", (oid,))
            event(con, oid, user["email"], "Revirtió el pedido: se sacaron los reemplazos posibles que había agregado.")
            audit(con, user["email"], "Revirtió pedido", f"#{oid}")
            con.commit()
            return order_dict(con, _order(con, oid))
        for it in res["done"]:
            engine.restore(it, it["bk"], it.get("obs_prev"))
            if it.get("links"):
                engine.restore_links(settings.docs_root, it["links"])
        engine.reset_cache()
        _remove_alts(con, params_of(o).get("to") or params_of(o)["code"], res.get("alts_added", []))
        if res.get("conv_saved"):
            con.execute("DELETE FROM conv WHERE key=?", (res["conv_saved"],))
        con.execute("UPDATE orders SET status='revertido' WHERE id=?", (oid,))
        event(con, oid, user["email"], f"Revirtió el pedido: {len(res['done'])} archivos vueltos a su estado anterior.")
        audit(con, user["email"], "Revirtió pedido", f"#{oid}")
        con.commit()
        return order_dict(con, _order(con, oid))
    finally:
        con.close()


def params_of(o):
    return json.loads(o["params"])


def run_cleanup(user, dry_run=False):
    require(user, "cleanup")
    if not dry_run:
        _writable()
    moved = engine.cleanup_folders(settings.docs_root, dry_run=dry_run)
    if not dry_run:
        con = connect()
        try:
            audit(con, user["email"], "Limpieza de obsoletos", "", f"{len(moved)} archivos a OBSOLETO")
            con.commit()
        finally:
            con.close()
    return {"moved": moved, "dry_run": dry_run}


def cleanup_preview(user):
    """Que archivos mandaria a OBSOLETO la limpieza, sin tocar nada. Lo consulta n8n cada semana para avisar."""
    require(user, "cleanup_view")
    moved = engine.cleanup_folders(settings.docs_root, dry_run=True)
    return {"count": len(moved), "moved": moved[:200], "truncated": len(moved) > 200, "portal_url": settings.portal_url}


def changes_log(user, q="", limit=300):
    """Historial de cambios de codigo ya aplicados: «Se cambio X por Y», cuando, quien lo pidio y quien lo aprobo."""
    require(user, "view")
    q = (q or "").strip().upper()
    con = connect()
    try:
        names = {r["email"]: r["name"] for r in con.execute("SELECT email,name FROM users")}
        out = []
        for r in con.execute("SELECT * FROM orders WHERE type='codigo' AND status IN ('completado','parcial','revertido') ORDER BY id DESC"):
            params = json.loads(r["params"])
            old, new = params.get("from", ""), params.get("to", "")
            if q and q not in old.upper() and q not in new.upper():
                continue
            res = json.loads(r["result"]) if r["result"] else {}
            out.append({"order": r["id"], "at": r["decided_at"], "from": old, "to": new, "status": r["status"],
                        "requested_by": names.get(r["created_by"], r["created_by"]),
                        "approved_by": names.get(r["decided_by"], r["decided_by"] or ""),
                        "files": len(res.get("done", [])), "cells": res.get("changes", 0),
                        "reason": params.get("reason", ""), "mode": params.get("rev_mode", "")})
            if len(out) >= limit:
                break
        return out
    finally:
        con.close()


def list_audit(user, limit=200):
    require(user, "audit")
    con = connect()
    try:
        return [dict(r) for r in con.execute("SELECT at,by,action,target,detail FROM audit ORDER BY id DESC LIMIT ?", (limit,))]
    finally:
        con.close()


def find_code(user, code):
    require(user, "view")
    return engine.simulate(settings.docs_root, code)


# ---------- catalogo de codigos ----------
def _alts_of(row):
    return json.loads(row["alts"]) if row else []


def _add_alts(con, code, alts, by):
    r = con.execute("SELECT * FROM catalog WHERE code=?", (code,)).fetchone()
    cur = _alts_of(r)
    added = [a for a in alts if a not in cur]
    if r:
        con.execute("UPDATE catalog SET alts=? WHERE code=?", (json.dumps(cur + added), code))
    else:
        con.execute("INSERT INTO catalog(code,alts,created_by,created_at) VALUES(?,?,?,?)", (code, json.dumps(added), by, now()))
    return added


def _remove_alts(con, code, alts):
    r = con.execute("SELECT * FROM catalog WHERE code=?", (code,)).fetchone()
    if r and alts:
        con.execute("UPDATE catalog SET alts=? WHERE code=?", (json.dumps([a for a in _alts_of(r) if a not in alts]), code))


def code_exists(code):
    code = engine.norm(code)
    con = connect()
    try:
        if con.execute("SELECT 1 FROM catalog WHERE code=?", (code,)).fetchone():
            return True
    finally:
        con.close()
    return code in engine.code_usage(settings.docs_root)


def _approve_reemplazos(con, o, user, comment):
    p = json.loads(o["params"])
    added = _add_alts(con, p["code"], p["alts"], o["created_by"])
    con.execute("UPDATE orders SET status='completado', result=?, decided_by=?, decided_at=? WHERE id=?",
                (json.dumps({"added": added}), user["email"], now(), o["id"]))
    event(con, o["id"], user["email"], "Aprobó el pedido." + (f" Comentario: {comment}" if comment else ""))
    event(con, o["id"], "sistema", f"Listo: {len(added)} reemplazos posibles agregados a {p['code']}.")
    audit(con, user["email"], "Aprobó pedido de reemplazos", f"#{o['id']}", f"{p['code']}: {', '.join(added)}")
    con.commit()
    return order_dict(con, _order(con, o["id"]))


def catalog_list(user, q="", limit=300):
    """Todos los codigos: los que aparecen en planillas mas los dados de alta en el catalogo."""
    require(user, "view")
    use = engine.code_usage(settings.docs_root)
    con = connect()
    try:
        cat = {r["code"]: r for r in con.execute("SELECT * FROM catalog")}
    finally:
        con.close()
    q = q.strip().upper()
    rows = []
    for code in sorted(set(use) | set(cat)):
        u, c = use.get(code), cat.get(code)
        desc = (c["desc"] if c and c["desc"] else (u["desc"] if u else ""))
        if q and q not in code and q not in desc.upper():
            continue
        rows.append({"code": code, "desc": desc, "kind": c["kind"] if c else "", "alts": _alts_of(c),
                     "cells": u["cells"] if u else 0, "folders": u["folders"] if u else 0, "in_catalog": bool(c)})
        if len(rows) >= limit:
            break
    return rows


def code_info(user, code):
    require(user, "view")
    code = engine.norm(code)
    rows = catalog_list(user, code)
    row = next((r for r in rows if r["code"] == code), None)
    if not row:
        raise LookupError("No existe ese código.")
    sim = engine.simulate(settings.docs_root, code)
    row["where"] = [{"folder": f, "files": [x["file"] for x in fs], "cells": sum(len(x["cells"]) for x in fs)} for f, fs in sim["folders"].items()]
    return row


def generate_code(user, code, desc, kind="", alts=None, request_id=None):
    require(user, "codegen")
    code, desc = engine.norm(code), (desc or "").strip()
    if not code or not desc:
        raise ValueError("Falta el código o la descripción.")
    if kind not in ("", "insumo", "producto"):
        raise ValueError("El tipo debe ser insumo o producto.")
    alts = _clean_alts(alts, code)
    con = connect()
    try:
        if con.execute("SELECT 1 FROM catalog WHERE code=?", (code,)).fetchone():
            raise ValueError(f"{code} ya está en el catálogo.")
        con.execute("INSERT INTO catalog(code,desc,kind,alts,created_by,created_at) VALUES(?,?,?,?,?,?)",
                    (code, desc, kind, json.dumps(alts), user["email"], now()))
        if request_id:
            r = con.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if not r:
                raise LookupError("No existe la solicitud.")
            con.execute("UPDATE requests SET status='resuelta', code=?, resolved_by=?, resolved_at=? WHERE id=?",
                        (code, user["email"], now(), request_id))
        audit(con, user["email"], "Generó código", code, desc)
        con.commit()
    finally:
        con.close()
    return {"code": code, "desc": desc, "kind": kind, "alts": alts}


# ---------- solicitudes de codigo ----------
def request_code(user, what, kind=""):
    require(user, "request")
    what = (what or "").strip()
    if len(what) < 5:
        raise ValueError("Contá qué código o insumo falta (al menos unas palabras).")
    con = connect()
    try:
        cur = con.execute("INSERT INTO requests(what,kind,by,at) VALUES(?,?,?,?)", (what, kind, user["email"], now()))
        audit(con, user["email"], "Avisó que falta un código", f"S{cur.lastrowid}", what)
        con.commit()
        return {"id": cur.lastrowid}
    finally:
        con.close()


def list_requests(user, status=None):
    require(user, "view")
    con = connect()
    try:
        q, a = "SELECT * FROM requests", []
        w = []
        if status:
            w.append("status=?"); a.append(status)
        if user["role"] == "Abastecimiento":        # ve solo los suyos
            w.append("by=?"); a.append(user["email"])
        if w:
            q += " WHERE " + " AND ".join(w)
        return [dict(r) for r in con.execute(q + " ORDER BY id DESC", a)]
    finally:
        con.close()


def reject_request(user, rid, note=""):
    require(user, "codegen")
    con = connect()
    try:
        r = con.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
        if not r:
            raise LookupError("No existe la solicitud.")
        if r["status"] != "abierta":
            raise ValueError("La solicitud ya fue atendida.")
        con.execute("UPDATE requests SET status='rechazada', note=?, resolved_by=?, resolved_at=? WHERE id=?",
                    (note, user["email"], now(), rid))
        audit(con, user["email"], "Rechazó solicitud de código", f"S{rid}", note)
        con.commit()
    finally:
        con.close()


# ---------- conversiones y busqueda ----------
def get_conv(user, old, new):
    require(user, "view")
    con = connect()
    try:
        r = con.execute("SELECT * FROM conv WHERE key=?", (f"{engine.norm(old)}>{engine.norm(new)}",)).fetchone()
        return {"factor": r["factor"], "note": r["note"]} if r else None
    finally:
        con.close()


def list_conv(user):
    require(user, "view")
    con = connect()
    try:
        return [dict(r) for r in con.execute("SELECT * FROM conv ORDER BY key")]
    finally:
        con.close()


def search(user, text):
    require(user, "view")
    if len(text.strip()) < 3:
        raise ValueError("Escribí al menos 3 letras.")
    return engine.search_full(settings.docs_root, text)


def revision_status(user):
    require(user, "revs")
    pend = engine.cleanup_folders(settings.docs_root, dry_run=True)
    data, _ = engine.scan(settings.docs_root)
    fams = {}
    for p in data:
        i = engine.file_info(p.name)
        if i:
            fams.setdefault(i["family"].split("|")[0], set()).add((i["kind"], i["n"], i["v"]))
    return {"vigentes": len(data), "con_varias_versiones": sum(1 for v in fams.values() if len(v) > 1), "por_limpiar": pend}


def links_report(user):
    require(user, "links")
    return engine.links_report(settings.docs_root)


def links_repair(user, dry_run=True):
    require(user, "links_fix")
    if not dry_run:
        _writable()
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    res = engine.repair_links(settings.docs_root, settings.backup_dir / f"accesos_{stamp}", dry_run=dry_run)
    if not dry_run and (res["fixed"] or res["failed"]):
        con = connect()
        try:
            audit(con, user["email"], "Reparó accesos directos", "",
                  f"{len(res['fixed'])} arreglados, {len(res['failed'])} con error (copias en respaldos/accesos_{stamp})")
            con.commit()
        finally:
            con.close()
    return res


def people(user):
    """Correo -> nombre, para mostrar nombres en vez de correos. Lo puede ver cualquier usuario."""
    require(user, "view")
    con = connect()
    try:
        return {r["email"]: r["name"] for r in con.execute("SELECT email,name FROM users")}
    finally:
        con.close()
