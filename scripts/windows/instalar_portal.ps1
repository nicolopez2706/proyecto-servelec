<#
 Instala el portal en esta computadora (PowerShell, como administrador no hace falta).
 Probado en Windows (laptop de Nico, 23/11/2026); falta probarlo en la computadora de la oficina.

 Uso:   .\scripts\windows\instalar_portal.ps1
        .\scripts\windows\instalar_portal.ps1 -DocsRoot "Z:\Doc_Productos"
 Qué hace: crea el entorno de Python, instala lo necesario, crea el archivo .env si no existe, corre las pruebas,
 crea la base y los usuarios (muestra una clave temporal distinta para cada uno, UNA sola vez: anotarlas).
#>
param([string]$DocsRoot = "")
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$back = Join-Path $raiz "backend"
Push-Location $back          # al terminar (o si falla algo) se vuelve a la carpeta donde estabas
try {

    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { throw "No encuentro Python. Instalar Python 3.11 o más nuevo desde python.org marcando 'Add Python to PATH' y volver a correr esto." }
    & python --version

    if (-not (Test-Path ".venv")) { & python -m venv .venv }
    $pyvenv = Join-Path $back ".venv\Scripts\python.exe"
    & $pyvenv -m pip install --upgrade pip
    & $pyvenv -m pip install -r requirements.txt

    if (-not (Test-Path ".env")) {
        Copy-Item ".env.example" ".env"
        Write-Host "Se creó backend\.env a partir del ejemplo."
    }
    if ($DocsRoot -ne "") {
        $lineas = Get-Content ".env" | Where-Object { $_ -notmatch "^\s*DOCS_ROOT=" }
        $lineas + "DOCS_ROOT=$DocsRoot" | Set-Content ".env" -Encoding UTF8
        Write-Host "DOCS_ROOT quedó en $DocsRoot"
    }

    Write-Host "`nCorriendo las pruebas..."
    & $pyvenv -m pytest -q tests
    if ($LASTEXITCODE -ne 0) { throw "Fallaron pruebas: no seguir hasta revisar por qué." }

    Write-Host "`nCreando usuarios (anotar las claves temporales, no se vuelven a mostrar):"
    & $pyvenv seed.py

    Write-Host "`nListo. Para probar:  .\scripts\windows\iniciar_portal.ps1   y abrir http://localhost:8000"
    Write-Host "Para dejarlo funcionando siempre: .\scripts\windows\tareas_programadas.ps1 (como administrador)."
} finally { Pop-Location }
