<#
 Deja que los compañeros de la red de la oficina entren al portal (puerto 8000). PowerShell COMO ADMINISTRADOR.
 NO ESTÁ PROBADO EN WINDOWS todavía.
 Solo abre para la red local (LocalSubnet), nunca para internet. Para cerrar de nuevo:  -Quitar
 El puerto de n8n (5678) NO se abre nunca: n8n se usa solo desde esta computadora.
#>
param([switch]$Quitar, [int]$Puerto = 8000, [string]$Nombre = "Portal SERVELEC")
$ErrorActionPreference = "Stop"
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "Abrir PowerShell con 'Ejecutar como administrador' y volver a correr esto." }
Get-NetFirewallRule -DisplayName $Nombre -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if ($Quitar) { Write-Host "Regla '$Nombre' eliminada."; return }
New-NetFirewallRule -DisplayName $Nombre -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Puerto -RemoteAddress LocalSubnet -Profile Domain,Private | Out-Null
Write-Host "Listo: la red local puede entrar al puerto $Puerto (perfiles Dominio y Privado)."
Write-Host "Si la red de la oficina figura como 'Pública' en Windows, cambiarla a 'Privada' o avisar a Airsat."
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    ForEach-Object { Write-Host ("  Dirección para los compañeros:  http://{0}:{1}" -f $_.IPAddress, $Puerto) }
Write-Host "La computadora tiene que tener IP fija (pedirle a Airsat una reserva) para que la dirección no cambie."
