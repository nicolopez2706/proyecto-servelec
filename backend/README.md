# Portal de documentación SERVELEC (prototipo real)

Backend (FastAPI + SQLite) y pantalla del portal en un mismo programa. Todo lo de la demo v7 que toca planillas
funciona de verdad: ingreso con roles por área, pedidos de cambio de código, aprobación eligiendo carpetas,
revisiones (R+1 con la anterior a OBSOLETO) o versiones nuevas (V+1), hoja "Control de cambios", cantidades,
respaldos, reversión, limpieza de obsoletos, catálogo de códigos con reemplazos posibles, avisos de códigos que
faltan, búsqueda y auditoría. Formatos: **.xlsx, .ods y .xls** (el .xls necesita LibreOffice instalado).

## Primera vez (Windows, PowerShell, dentro de `C:\proyecto-servelec\backend`)
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # abrir .env y revisar DOCS_ROOT (SECRET_KEY se genera sola)
python seed.py                # crea la base y los 5 usuarios; muestra UNA vez la clave temporal de cada uno
python crear_docs_prueba.py   # planillas de prueba (NO apuntar a la carpeta real todavía)
python -m pytest -q tests     # deberían pasar todas
uvicorn app.main:app --reload
```
Después abrí **http://localhost:8000** en el navegador. Ingresá con `n.lopez@servelec.com.ar` y la clave temporal que mostró `seed.py`; el portal te pide elegir una propia.

Para probar los roles, ingresá con los otros usuarios (cada uno con su clave temporal): `abastecimiento@servelec.com.ar`,
`l.bazzoli@servelec.com.ar` (Ingeniería), `p.delfino@servelec.com.ar` y `v.delfino@servelec.com.ar` (Gerencia).
En **Estado del sistema** (solo Sistemas) se ve si encontró LibreOffice y qué carpeta está usando.

## Qué probar
1. Ingresá como Ingeniería → *Cambiar un código*: `DIS206AA` por `DIS206BA`, con cantidades x2 → *Ver qué se va a cambiar* → *Enviar a aprobación*.
2. Ingresá como Gerencia → abrí el pedido → sacá una o dos carpetas → *Aprobar*. Miren las carpetas de `docs_prueba`:
   los archivos nuevos (R1), la carpeta OBSOLETO y la hoja "Control de cambios" dentro de cada archivo.
3. *Revertir* (Gerencia o Sistemas): todo vuelve a como estaba.
4. Como Abastecimiento: *Códigos que faltan* → avisar. Como Ingeniería: generar el código. Probá también *Reemplazos posibles*.

## Pasar a la empresa
Solo cambia `.env`: `DOCS_ROOT=Z:\Doc_Productos` y una `SECRET_KEY` nueva. No hay rutas en el código.
**Antes de apuntar a la carpeta real, probá con una copia**: cada archivo se respalda en `DATA_DIR\respaldos\pedido_N`
antes de tocarlo y todo pedido se puede revertir, pero conviene validar con datos reales sin riesgo.

## Accesos directos
- El portal recorre `DOCS_ROOT` (y las carpetas de `EXTRA_ROOTS`) y **modifica siempre el archivo real, una sola vez**, aunque tenga varios accesos directos.
- Lee `.lnk`, `.url` y enlaces simbólicos. Si apuntan a una carpeta, esa carpeta ya está cubierta por el recorrido; si apuntan a un archivo, se anota "desde dónde se llega".
- Cuando un archivo pasa a la revisión siguiente (R0 → R1), los `.lnk` que apuntaban al archivo se actualizan para abrir el nuevo (se les cambia también el nombre si llevaba el del documento). Se guarda copia y **Revertir** los deja como estaban. Con una versión nueva (V+1) no se tocan, porque la anterior sigue vigente.
- Pantalla **Accesos directos** (Control): muestra cuáles funcionan, cuáles apuntan a una revisión vieja (se pueden reparar con un botón), cuáles están rotos y qué línea agregar al `.env` si hace falta (`LINK_MAP`, `EXTRA_ROOTS`).
- En la laptop, si los accesos directos dicen `Z:\Doc_Productos\...`, poné en `.env`: `LINK_MAP=Z:\Doc_Productos=C:\proyecto-servelec\docs_prueba`. En la empresa no hace falta.

## Conectar a las carpetas reales (paso a paso y sin riesgo)
1. **Inventario (solo lee):** poné en `.env` `DOCS_ROOT=Z:\Doc_Productos` (o una copia) y corré `python inventario.py`. Muestra cuántas planillas hay, de qué formato, cuáles no se pueden leer, cuáles no tienen revisión en el nombre y cómo están los accesos directos. El detalle queda en `data\inventario_problemas.csv`.
2. **Modo solo lectura:** con `READ_ONLY=1` en `.env` el portal busca, simula y recibe pedidos, pero no aplica ni revierte nada.
3. **Primer cambio real:** sacá `READ_ONLY`, probá con **una copia** de unas pocas carpetas y recién después apuntá al servidor. Cada archivo se respalda antes de tocarse y todo se puede revertir desde el pedido.
4. Accesos directos a **Google Sheets** (`.url`, `.gsheet`): se detectan y se listan, pero todavía no se modifican (etapa siguiente: cuenta de servicio de Google).

## Sugerencias en las planillas
- Al cambiar un código con códigos sugeridos, o con un pedido de **Reemplazos posibles**, se puede escribir una columna **«Sugerencias»** en cada fila donde está el código. Si la columna no existe se crea; si ya tenía sugerencias se suman sin repetir. Funciona en `.xlsx`, `.ods` y `.xls`; queda registrado en «Control de cambios» y se revierte igual que cualquier cambio.

## Cómo funciona (resumen)
- `app/engine.py`: lee y cambia planillas, renombra revisiones, arma el Control de cambios. Guarda un índice de las celdas
  (`DATA_DIR\indice_celdas.json`) para que las búsquedas siguientes sean rápidas: solo relee lo que cambió.
  La primera búsqueda sobre todo el servidor puede tardar (los .xls se convierten con LibreOffice una sola vez).
- `app/ods.py`: edita el .ods por dentro sin perder formato.
- `app/services.py`: reglas del negocio y permisos (los mismos de la demo v7, en `app/permissions.py`).
- `app/router.py`: la API. `app/main.py`: el servidor. `app/static/index.html`: la pantalla.
- Quien pidió un pedido no lo puede aprobar (salvo Sistemas). Al aprobar se pueden sacar carpetas, no agregar:
  solo se cambian carpetas donde el código existe.
- Los .xls se convierten: el archivo nuevo queda como **.xlsx** y el .xls anterior va a OBSOLETO.
- Archivos abiertos por otra persona (`~$archivo`) se omiten y quedan como pendientes.

## Velocidad de las búsquedas
- El índice de celdas se guarda en `DATA_DIR\indice_celdas.json` y solo se relee lo que cambió. La **primera** lectura de todas las planillas es lenta (los `.xls` se convierten con LibreOffice); después es inmediata.
- Al arrancar el portal precalienta el índice en segundo plano, y Buscar y Códigos reusan el recorrido de las carpetas durante `SCAN_TTL` segundos (30 por defecto). Los cambios y las simulaciones antes de aprobar siempre recorren de nuevo.
- Si el servidor tiene carpetas enormes sin planillas (software, documentación web), excluirlas con `EXCLUDE_DIRS` acelera todo el recorrido.

## Asistente
Pantalla *Asistente*: entiende frases en español («cambiar X por Y», «qué código es…», «en qué planillas está…», «cómo viene el pedido N»). Funciona con reglas, sin IA externa; arma pedidos con **botón de confirmación** y nunca aprueba, revierte ni administra. `app/assistant.py` (si más adelante se usa un modelo de lenguaje, solo reemplazaría `interpretar()`).

## Seguridad y claves
- Cada usuario nace con una clave temporal distinta (`seed.py`) y el portal no lo deja seguir hasta que la cambia (mínimo 10 caracteres, sin claves comunes).
- Al dar de alta desde *Usuarios*: si Sistemas escribe la clave inicial (por ejemplo una que la persona ya conoce), esa clave se **mantiene** y cambiarla es opcional (hay una casilla para obligarla). Si la deja vacía, el portal genera una temporal que sí hay que cambiar. Ojo: una clave tipo DNI es fácil de adivinar; el bloqueo por intentos ayuda, pero conviene que la cambien.
- Los usuarios **desactivados** se pueden eliminar (botón *Eliminar* en *Usuarios*); su historial se conserva.
- Sistemas puede restablecer la clave de alguien desde *Usuarios* (se muestra una temporal una sola vez); si se perdió la de Sistemas: `python reset_clave.py correo`.
- 5 intentos fallidos seguidos bloquean ese correo 15 minutos (`LOGIN_MAX_FAILS`, `LOGIN_LOCK_MINUTES`).
- `SECRET_KEY` se genera sola en `DATA_DIR\secret.key` si no se define. Los valores de ejemplo no se aceptan. Si cambia, hay que regenerar el token de n8n (`python servicio_n8n.py`).

## Cuidados con las planillas
- **`.xlsx`**: al guardar, el portal compara lo que tenía el original con lo que quedó. Si se perdieran imágenes, formas o cuadros de texto, gráficos, tablas dinámicas o segmentaciones, **no toca esa planilla** y lo informa en el pedido (cambiarla a mano). `PERMITIR_PERDIDA_XLSX=1` lo permite igual (no recomendado). Se necesita `Pillow` (está en `requirements.txt`): sin él las imágenes se perderían en silencio.
- **Un cambio de planillas a la vez**: si dos personas aprueban pedidos al mismo tiempo, el portal los ejecuta uno después del otro; y dos aprobaciones del mismo pedido ejecutan una sola vez.

## Control contra Magnus
*Control → Control contra Magnus*. Se exporta Magnus a una planilla o CSV y se pone la ruta en `MAGNUS_EXPORT`. El portal detecta las columnas (o se indican con `MAGNUS_COL_CODIGO` / `MAGNUS_COL_DESC`), compara con las planillas y el catálogo y deja un informe Excel: *Faltan en Magnus*, *Descripción distinta*, *Solo en Magnus*. Solo informa. `app/magnus.py`.

## Respaldo
`python respaldo.py DESTINO [--n8n CARPETA]` copia base, `.env`, `secret.key` y datos de n8n; `--restaurar CARPETA` vuelve atrás. Detalle en `..\docs\INSTALACION_OFICINA.md`.

## Todavía no está
- Conexión directa con Magnus (hoy es por archivo exportado) y con Google Sheets (cuenta de servicio de Google).
- Ingreso con la cuenta Google de la empresa (hoy correo y clave).
- Acceso desde internet con HTTPS (pendiente de decidir con Airsat: VPN o Cloudflare Tunnel).
