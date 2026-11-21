# Proyecto SERVELEC: automatización de códigos en documentación

Portal y motor que cambian un código por otro (X → Y) en las planillas de documentación
(`.ods`, `.xls`, `.xlsx`) con pedido, aprobación, respaldo, auditoría y posibilidad de revertir.

## Qué hay en cada carpeta

| Carpeta | Contenido |
|---|---|
| `backend/` | Portal y motor (FastAPI + SQLite). Lógica en `app/`, pruebas en `tests/`. Ver `backend/README.md`. |
| `scripts/` | `revisar_secretos.py` (frena claves antes de subir a GitHub), `windows\` (instalar, iniciar, abrir puerto, tareas programadas) y scripts de práctica anteriores al portal. |
| `workflows/` | Flujos de n8n listos para importar (`portal-avisos.json`, `portal-limpieza-semanal.json`, `portal-magnus-semanal.json`). |
| `docs/` | `INSTALACION_OFICINA.md` (instalar en la computadora de la oficina) y `GUIA_POR_AREA.md`. |

## Arranque rápido (Windows, PowerShell, desde `backend`)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # completar DOCS_ROOT (SECRET_KEY se genera sola si no se pone)
python seed.py              # usuarios (clave temporal distinta para cada uno, se muestra una sola vez)
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

### Limpieza semanal (paso A4)

`workflows/portal-limpieza-semanal.json`: cada lunes a las 9:00 n8n le pregunta al portal (`GET /api/cleanup/preview`, solo mira)
qué archivos de revisiones viejas irían a OBSOLETO y, si hay alguno, arma el aviso. **La limpieza en sí la ejecuta una persona**
de Ingeniería o Sistemas desde el portal; el usuario de servicio no puede. Hay que pegar su token (`python servicio_n8n.py`)
en el nodo HTTP, reemplazando `PEGAR_ACA_EL_TOKEN`. El último nodo de cada flujo es «Enviar correo» (Gmail, correo con formato HTML): al importar hay que elegirle la credencial de Gmail. Los avisos de prueba salen a `cobranzas@servelec.com.ar`; para producción hay que cambiar el destinatario y usar una cuenta de la empresa.

### Control contra Magnus (paso A7)

`workflows/portal-magnus-semanal.json`: cada lunes a las 9:30 n8n le pide al portal (`POST /api/magnus/run`) que compare los códigos de las
planillas y el catálogo con la exportación de Magnus (`MAGNUS_EXPORT` en `.env`) y, si hay diferencias, manda el aviso. El control solo
**informa** (informe en Excel descargable desde *Control → Control contra Magnus*); no modifica planillas ni Magnus.

## Seguridad: lo que hay que saber

- Claves guardadas con scrypt (no se pueden ver). Cada usuario nace con una clave temporal distinta y debe cambiarla; 5 intentos fallidos bloquean el correo 15 minutos.
- `SECRET_KEY` se genera sola en `data\secret.key` si no se define. **Si cambia, hay que generar de nuevo el token de n8n** (`python servicio_n8n.py`).
- `python scripts\revisar_secretos.py --hook` instala la revisión automática antes de cada commit.
- Instalación en la oficina, respaldos y qué hacer si algo falla: `docs\INSTALACION_OFICINA.md`.

## Qué NO se sube a este repositorio

El archivo `.env`, la base de datos y los respaldos (`backend/data/`) y las planillas reales o de prueba
(`docs_prueba/`). Están en `.gitignore`.

## Estado y hoja de ruta

El estado y los próximos pasos viven en el Proyecto de Claude "automatizacion VICTORIA codigos".
