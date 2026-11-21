<#
 Prende el portal. Es lo que ejecuta la tarea programada al arrancar la computadora.
 NO ESTÁ PROBADO EN WINDOWS todavía.
 -SoloEstaPC  : el portal solo se abre desde esta computadora (127.0.0.1). Sin eso lo pueden abrir los demás de la red.
#>
param([switch]$SoloEstaPC, [int]$Puerto = 8000)
$ErrorActionPreference = "Stop"
$back = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "backend"
Set-Location $back
$pyvenv = Join-Path $back ".venv\Scripts\python.exe"
$direccion = if ($SoloEstaPC) { "127.0.0.1" } else { "0.0.0.0" }
$salida = Join-Path $back "data\portal.log"
$errores = Join-Path $back "data\portal_errores.log"
New-Item -ItemType Directory -Force (Split-Path $salida) | Out-Null
# uvicorn escribe sus avisos por la salida de errores. Start-Process con redirección a archivos evita que Windows PowerShell 5.1
# los tome como errores del script (con 2>&1 lo cortaría). El código de salida se devuelve para que la tarea programada
# lo reinicie si el portal se cae. Los registros se vuelven a empezar en cada arranque.
$p = Start-Process -FilePath $pyvenv -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", $direccion, "--port", $Puerto) `
    -WorkingDirectory $back -RedirectStandardOutput $salida -RedirectStandardError $errores -NoNewWindow -Wait -PassThru
exit $p.ExitCode
