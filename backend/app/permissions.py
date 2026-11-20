"""Roles y permisos. Es la misma matriz de la demo v7."""

ROLES = ["Abastecimiento", "Ingeniería", "Gerencia", "Sistemas"]

# Usuario de servicio (n8n, asistente): puede mirar, pedir cambios y avisar codigos que faltan. Nunca aprueba, revierte ni administra.
SERVICE_ROLE = "Automatización"

PERMS = {
    "view": ROLES + [SERVICE_ROLE],
    "create": ROLES + [SERVICE_ROLE],
    "request": ["Abastecimiento", "Sistemas", SERVICE_ROLE],          # avisar que falta un codigo
    "codegen": ["Ingeniería", "Sistemas"],              # generar codigos
    "approve": ["Ingeniería", "Gerencia", "Sistemas"],
    "pickfolders": ["Ingeniería", "Gerencia", "Sistemas"],
    "retry": ["Ingeniería", "Gerencia", "Sistemas"],
    "revert": ["Gerencia", "Sistemas"],
    "revs": ["Ingeniería", "Gerencia", "Sistemas"],
    "cleanup": ["Ingeniería", "Sistemas"],
    "cleanup_view": ["Ingeniería", "Sistemas", SERVICE_ROLE],        # solo mirar que habria para limpiar (n8n lo usa cada semana)
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
