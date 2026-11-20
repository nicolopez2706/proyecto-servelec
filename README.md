# Proyecto SERVELEC: automatización de códigos en documentación

Portal y motor que cambian un código por otro (X → Y) en las planillas de documentación
(`.ods`, `.xls`, `.xlsx`) con pedido, aprobación, respaldo, auditoría y posibilidad de revertir.

## Qué hay en cada carpeta

| Carpeta | Contenido |
|---|---|
| `backend/` | Portal y motor (FastAPI + SQLite). Lógica en `app/`, pruebas en `tests/`. Ver `backend/README.md`. |
| `scripts/` | Scripts sueltos de práctica anteriores al portal. |
| `workflows/` | Flujos de n8n (todavía vacío). |

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

## Qué NO se sube a este repositorio

El archivo `.env`, la base de datos y los respaldos (`backend/data/`) y las planillas reales o de prueba
(`docs_prueba/`). Están en `.gitignore`.

## Estado y hoja de ruta

El estado y los próximos pasos viven en el Proyecto de Claude "automatizacion VICTORIA codigos".
