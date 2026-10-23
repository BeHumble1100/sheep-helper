param([string]$Name = 'SheepHelper')

$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot

python -m PyInstaller `
    --noconfirm `
    --onefile `
    --windowed `
    --name $Name `
    --paths src `
    --exclude-module numpy `
    --add-data 'src/sheep_helper/__init__.py;sheep_helper' `
    --add-data 'src/sheep_helper/capture_state.py;sheep_helper' `
    --add-data 'src/sheep_helper/extractor.py;sheep_helper' `
    --add-data 'src/sheep_helper/game_capture_addon.py;sheep_helper' `
    --add-data 'src/sheep_helper/match_data.py;sheep_helper' `
    --add-data 'src/sheep_helper/seed_recovery_addon.py;sheep_helper' `
    --add-data 'src/sheep_helper/redaction.py;sheep_helper' `
    src/sheep_helper/desktop.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 构建失败，退出码 $LASTEXITCODE"
}

Write-Host "EXE 已生成：$projectRoot\dist\$Name.exe"
