$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Missing .venv. Install the project capture environment first.'
}
$env:PATH = (Join-Path $projectRoot '.venv\Scripts') + ';' + $env:PATH
& $pythonPath -m sheep_helper.desktop --capture-dir (Join-Path $projectRoot 'captured-layouts') @args
exit $LASTEXITCODE
