# Proyecto SERVELEC: automatización de códigos en documentación

Portal y motor que cambian un código por otro (X → Y) en las planillas de documentación
(`.ods`, `.xls`, `.xlsx`) con pedido, aprobación, respaldo, auditoría y posibilidad de revertir.

## Qué hay en cada carpeta

| Carpeta | Contenido |
|---|---|
| `backend/` | Portal y motor (FastAPI + SQLite). Lógica en `app/`, pruebas en `tests/`. Ver `backend/README.md`. |
| `scripts/` | Scripts sueltos de práctica anteriores al portal. |
| `workflows/` | Flujos de n8n listos para importar (`portal-avisos.json`). |

## Arranque rápido (Windows, PowerShell, desde `backend`)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # completar SECRET_KEY y DOCS_ROOT
python seed.py              # usuarios de ejemplo
uvicorn app.main:app --port 8000
```

Abrir http://127.0.0.1:8000. Para leer planillas `.xls` hace falta LibreOffice instalado.

## Conexión con n8n (paso A3)

El portal avisa a n8n cada vez que pasa algo importante (pedido creado, aprobado, ejecutado, rechazado, cancelado,
revertido, código que falta, código generado, limpieza). Es un POST con JSON a un webhook, en segundo plano: si n8n está
apagado, el portal sigue igual (reintenta 3 veces y deja una línea en la consola).

1. En `backend\.env`: `N8N_WEBHOOK_URL`, `N8N_WEBHOOK_SECRET` y `PORTAL_URL` (ejemplos en `.env.example`). Reiniciar el portal.
2. En n8n: *Import from file* → `workflows/portal-avisos.json`. Poner la misma clave en el nodo "Verificar y armar texto".
   Activar el flujo (la URL de producción es `.../webhook/portal-eventos`; la de prueba lleva `webhook-test`).
3. En el portal: *Estado del sistema* → *Enviar aviso de prueba*.
4. Para que n8n **consulte** el portal (lo que usará el asistente): `python servicio_n8n.py` crea el usuario de servicio
   «Automatización» (puede ver, simular, pedir cambios y avisar códigos faltantes; **nunca aprueba, revierte ni administra**)
   y muestra su token. En n8n va como encabezado `Authorization: Bearer <token>`. `python servicio_n8n.py --desactivar` lo apaga.

## Qué NO se sube a este repositorio

El archivo `.env`, la base de datos y los respaldos (`backend/data/`) y las planillas reales o de prueba
(`docs_prueba/`). Están en `.gitignore`.

## Estado y hoja de ruta

El estado y los próximos pasos viven en el Proyecto de Claude "automatizacion VICTORIA codigos".
