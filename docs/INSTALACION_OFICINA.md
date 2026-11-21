# Instalar el portal en la computadora de la oficina (Etapa B)

Guía paso a paso, pensada para hacerla sin experiencia previa. Los scripts de `scripts\windows\` **no se probaron todavía
en Windows**: hacer primero una vuelta completa en la laptop y anotar cualquier mensaje de error.

## 0. Qué se necesita (lo que depende de otras personas)

| Qué | Quién | Para qué |
|---|---|---|
| Una computadora (o servidor) prendida siempre, con Windows | Airsat / oficina | Ahí corre el portal |
| IP fija para esa computadora (reserva en el router/DHCP) | Airsat | Que la dirección que usan los compañeros no cambie |
| Una cuenta de Windows de servicio (ej. `svc_portal`) con permiso de **escritura** en la carpeta de documentos | Airsat | El portal modifica planillas con esa cuenta |
| Permiso para abrir el puerto 8000 solo en la red local | Airsat | Que los compañeros entren |
| Python 3.11+ y LibreOffice (para planillas `.xls`) | Nico | Motor del portal |
| Carpeta de destino para los respaldos (otro disco o carpeta de red) | Airsat | Que un respaldo sobreviva si se rompe la computadora |
| Cuenta de Gmail de la empresa para los avisos de n8n | Airsat | Reemplaza el correo de prueba |

## 1. Instalar

1. Copiar el proyecto a la computadora (`git clone https://github.com/nicolopez2706/proyecto-servelec` o copiar la carpeta) en `C:\proyecto-servelec`.
2. PowerShell, dentro de `C:\proyecto-servelec`:
   ```powershell
   .\scripts\windows\instalar_portal.ps1 -DocsRoot "Z:\Doc_Productos"
   ```
   Crea el entorno de Python, instala lo necesario, corre las pruebas y crea los usuarios.
   **Anotá las claves temporales que muestra** (una por persona, aparecen una sola vez). Cada persona tiene que cambiarla al primer ingreso.
3. Antes de apuntar a la carpeta real: poner `READ_ONLY=1` en `backend\.env`, correr `python inventario.py` y mirar qué encuentra (ver `backend\README.md`).
   Probar con una **copia** de unas pocas carpetas antes del primer cambio real.

## 2. Dejarlo funcionando solo

PowerShell **como administrador**:
```powershell
.\scripts\windows\abrir_puerto.ps1
.\scripts\windows\tareas_programadas.ps1 -Cuenta "DOMINIO\svc_portal" -DestinoRespaldo "\\servidor\respaldos\portal" -N8n "C:\Users\svc_portal\.n8n"
```
- `abrir_puerto.ps1` muestra la dirección que usarán los compañeros (`http://IP:8000`). Solo abre para la red local.
- `tareas_programadas.ps1` prende el portal al arrancar la computadora y hace un respaldo todos los días a las 02:00.
- **n8n no se expone**: se usa solo desde esa computadora (`http://localhost:5678`). Su puerto 5678 no se abre.

## 3. Primer día

1. Entrar como Sistemas, ir a *Estado del sistema*: tiene que figurar la carpeta de documentos, LibreOffice y n8n.
2. Cada persona entra con su clave temporal y elige la suya.
3. `python servicio_n8n.py` → token para n8n (ver `README.md` de la raíz). Importar los flujos de `workflows\` y ponerles el token y la clave compartida.
4. **Si cambió `SECRET_KEY` (o se generó `data\secret.key` nueva), el token de n8n deja de servir**: generar uno nuevo y pegarlo en los flujos.

## 4. Respaldos y volver atrás

- Automático: ver arriba. A mano: `python respaldo.py \\servidor\respaldos\portal --n8n C:\Users\svc_portal\.n8n`
- Guarda la base del portal, `.env`, `secret.key` y, de n8n, los flujos y su **clave de cifrado** (sin ella las credenciales de n8n no se pueden recuperar).
- Los respaldos contienen claves: el destino debe tener acceso restringido.
- Restaurar (con el portal apagado): `python respaldo.py --restaurar "\\servidor\respaldos\portal\portal_2026-..."`
- Cada pedido, además, guarda copia de cada planilla antes de tocarla (`data\respaldos\pedido_N`) y se puede revertir desde el portal.

## 5. Si algo falla

| Síntoma | Causa probable |
|---|---|
| Los compañeros no abren la página | Falta `abrir_puerto.ps1`, la red figura como «Pública», o la IP cambió |
| «Acceso denegado» al aprobar un pedido | La cuenta con la que corre el portal no tiene escritura en la carpeta de documentos |
| Un pedido queda como «falló» con «se perdería contenido» | Esa planilla `.xlsx` tiene formas, imágenes o tablas dinámicas que el portal no sabe conservar: se cambia a mano. El archivo no se tocó |
| Nadie recuerda la clave de Sistemas | En la computadora del portal: `python reset_clave.py correo@servelec.com.ar` |
| El portal se cerró a mitad de un pedido | Al volver a prender, el pedido queda «pendiente» (si no tocó nada) o «falló» con la ruta de los respaldos para revisar |
| n8n no recibe avisos | `N8N_WEBHOOK_URL` mal puesta, flujo inactivo, o la clave compartida distinta en el flujo |

## 6. Antes de subir cambios a GitHub

`python scripts\revisar_secretos.py` (o `--hook` para que se corra solo antes de cada commit). Frena si hay claves, tokens o bases de datos.
