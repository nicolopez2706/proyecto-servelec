<#
 Deja el portal y el respaldo funcionando solos. PowerShell COMO ADMINISTRADOR.
 NO ESTÁ PROBADO EN WINDOWS todavía.

 Crea dos tareas:
   "Portal SERVELEC"          prende el portal al arrancar la computadora y lo vuelve a prender si se cae.
   "Respaldo Portal SERVELEC" todos los días a las 02:00 copia la base, el .env y (si se indica) n8n.

 Uso:  .\scripts\windows\tareas_programadas.ps1 -Cuenta "SERVELEC\svc_portal" -DestinoRespaldo "\\servidor\respaldos\portal" -N8n "C:\Users\svc_portal\.n8n"
 La cuenta tiene que poder ESCRIBIR en la carpeta de documentos (DOCS_ROOT) y en el destino del respaldo: si no, los
 cambios de planillas fallan con "acceso denegado". Pide la clave de esa cuenta una vez (no queda guardada en ningún archivo).
 Para sacar las tareas:  -Quitar
#>
param([string]$Cuenta = "", [string]$DestinoRespaldo = "", [string]$N8n = "", [switch]$Quitar)
$ErrorActionPreference = "Stop"
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "Abrir PowerShell con 'Ejecutar como administrador' y volver a correr esto." }
$t1 = "Portal SERVELEC"; $t2 = "Respaldo Portal SERVELEC"
if ($Quitar) {
    foreach ($t in $t1, $t2) { Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue }
    Write-Host "Tareas eliminadas."; return
}
if ($Cuenta -eq "") { throw "Falta -Cuenta (la cuenta con la que corre el portal, por ejemplo SERVELEC\svc_portal)." }
$DestinoRespaldo = $DestinoRespaldo.TrimEnd('\')      # una barra final rompería las comillas del comando
$N8n = $N8n.TrimEnd('\')
$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$back = Join-Path $raiz "backend"
$cred = Get-Credential -UserName $Cuenta -Message "Clave de la cuenta $Cuenta"
$clave = $cred.GetNetworkCredential().Password

$accion = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $back `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot 'iniciar_portal.ps1')`""
$disparador = New-ScheduledTaskTrigger -AtStartup
$ajustes = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
Unregister-ScheduledTask -TaskName $t1 -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $t1 -Action $accion -Trigger $disparador -Settings $ajustes -User $Cuenta -Password $clave -RunLevel Highest | Out-Null
Write-Host "Tarea '$t1' creada (se prende sola al arrancar)."

if ($DestinoRespaldo -ne "") {
    $pyvenv = Join-Path $back ".venv\Scripts\python.exe"
    $argumentos = "respaldo.py `"$DestinoRespaldo`""
    if ($N8n -ne "") { $argumentos += " --n8n `"$N8n`"" }
    $accion2 = New-ScheduledTaskAction -Execute $pyvenv -Argument $argumentos -WorkingDirectory $back
    $disparador2 = New-ScheduledTaskTrigger -Daily -At 2:00AM
    Unregister-ScheduledTask -TaskName $t2 -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $t2 -Action $accion2 -Trigger $disparador2 -User $Cuenta -Password $clave | Out-Null
    Write-Host "Tarea '$t2' creada (todos los días a las 02:00)."
} else {
    Write-Host "Sin respaldo automático: volver a correr con -DestinoRespaldo para crearlo."
}
$clave = $null
Write-Host "Para probar ya mismo:  Start-ScheduledTask -TaskName '$t1'"
