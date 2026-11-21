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
$log = Join-Path $back "data\portal.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
"[{0}] Prendiendo el portal en {1}:{2}" -f (Get-Date), $direccion, $Puerto | Add-Content $log
& $pyvenv -m uvicorn app.main:app --host $direccion --port $Puerto 2>&1 | Tee-Object -FilePath $log -Append
