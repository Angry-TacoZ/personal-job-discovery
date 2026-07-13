$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found. Run: py -3.12 -m venv .venv"
}

Push-Location $ProjectRoot
try {
    & $Python -m job_discovery --config config/companies.yml scan
    if ($LASTEXITCODE -ne 0) {
        throw "Job scan failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

