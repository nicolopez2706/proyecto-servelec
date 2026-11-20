"""Roles y permisos. Es la misma matriz de la demo v7."""

ROLES = ["Abastecimiento", "Ingeniería", "Gerencia", "Sistemas"]

PERMS = {
    "view": ROLES,
    "create": ROLES,
    "request": ["Abastecimiento", "Sistemas"],          # avisar que falta un codigo
    "codegen": ["Ingeniería", "Sistemas"],              # generar codigos
    "approve": ["Ingeniería", "Gerencia", "Sistemas"],
    "pickfolders": ["Ingeniería", "Gerencia", "Sistemas"],
    "retry": ["Ingeniería", "Gerencia", "Sistemas"],
    "revert": ["Gerencia", "Sistemas"],
    "revs": ["Ingeniería", "Gerencia", "Sistemas"],
    "cleanup": ["Ingeniería", "Sistemas"],
    "magnus": ROLES,
    "magnus_run": ["Ingeniería", "Sistemas"],
    "magnus_alta": ["Abastecimiento", "Ingeniería", "Sistemas"],
    "audit": ["Gerencia", "Sistemas"],
    "links": ["Ingeniería", "Gerencia", "Sistemas"],    # ver el estado de los accesos directos
    "links_fix": ["Sistemas"],                           # repararlos
    "admin": ["Sistemas"],
}


class PermissionDenied(Exception):
    pass


def can(role, perm):
    return role in PERMS.get(perm, [])


def require(user, perm):
    if not user or not can(user["role"], perm):
        raise PermissionDenied(f"Tu área ({user['role'] if user else '—'}) no tiene permiso para esto.")
