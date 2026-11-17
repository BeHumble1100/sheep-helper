param()

$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot

$Name = 'SheepHelper'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $pythonPath = (Get-Command python -ErrorAction Stop).Source
}
$stagingDir = Join-Path $projectRoot 'build\release-dist'
$specDir = Join-Path $projectRoot 'build\release-spec'
$releaseDir = Join-Path $projectRoot 'dist'
$nativeDir = Join-Path $projectRoot 'build\native'
New-Item -ItemType Directory -Path $nativeDir -Force | Out-Null
$nativeExe = Join-Path $nativeDir 'CompactSolver.exe'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
& $compiler /nologo /optimize+ /r:System.Web.Extensions.dll "/out:$nativeExe" "$projectRoot\src\sheep_helper\native\CompactSolver.cs"
if ($LASTEXITCODE -ne 0) { throw 'Compact solver compilation failed; existing release preserved.' }

& $pythonPath -m PyInstaller `
    --noconfirm `
    --onefile `
    --windowed `
    --name $Name `
    --distpath $stagingDir `
    --specpath $specDir `
    --paths "$projectRoot\src" `
    --exclude-module numpy `
    --add-binary "$nativeExe;sheep_helper" `
    --add-data "$projectRoot/src/sheep_helper/__init__.py;sheep_helper" `
    --add-data "$projectRoot/src/sheep_helper/capture_state.py;sheep_helper" `
    --add-data "$projectRoot/src/sheep_helper/extractor.py;sheep_helper" `
    --add-data "$projectRoot/src/sheep_helper/game_capture_addon.py;sheep_helper" `
    --add-data "$projectRoot/src/sheep_helper/match_data.py;sheep_helper" `
    --add-data "$projectRoot/src/sheep_helper/seed_recovery_addon.py;sheep_helper" `
    --add-data "$projectRoot/src/sheep_helper/redaction.py;sheep_helper" `
    "$projectRoot/src/sheep_helper/desktop.py"

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 构建失败，退出码 $LASTEXITCODE"
}

$stagedExe = Join-Path $stagingDir 'SheepHelper.exe'
foreach ($checkArgs in @(
    @('--self-test', (Join-Path $projectRoot 'build\release-self-test.json')),
    @('--smoke', '--no-start-capture')
)) {
    $check = Start-Process -FilePath $stagedExe -ArgumentList $checkArgs -WindowStyle Hidden -PassThru
    if (-not $check.WaitForExit(60000)) {
        Stop-Process -Id $check.Id -ErrorAction SilentlyContinue
        throw 'EXE validation timed out; existing release preserved.'
    }
    if ($check.ExitCode -ne 0) { throw "EXE validation failed: $($check.ExitCode)" }
}

$running = Get-Process -Name 'SheepHelper*' -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and (Split-Path -Parent $_.Path) -eq $releaseDir }
if ($running) { throw 'Close the running SheepHelper before updating; validated build is in build\release-dist.' }

New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
$oldExes = @(Get-ChildItem -LiteralPath $releaseDir -Filter 'SheepHelper*.exe' -File)
$backupDir = Join-Path $projectRoot ('build\exe-backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
$canonicalExe = Join-Path $releaseDir 'SheepHelper.exe'
$pendingExe = Join-Path $releaseDir 'SheepHelper.pending'
Copy-Item -LiteralPath $stagedExe -Destination $pendingExe -Force
if ((Get-FileHash -LiteralPath $pendingExe).Hash -ne (Get-FileHash -LiteralPath $stagedExe).Hash) {
    throw 'EXE checksum mismatch; existing release preserved.'
}
if ($oldExes.Count -gt 0) {
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
}
try {
    foreach ($oldExe in $oldExes) {
        Move-Item -LiteralPath $oldExe.FullName -Destination $backupDir
    }
    Move-Item -LiteralPath $pendingExe -Destination $canonicalExe
} catch {
    # Retain a usable canonical entry if archiving or promotion was interrupted.
    $previousExe = Join-Path $backupDir 'SheepHelper.exe'
    if (-not (Test-Path -LiteralPath $canonicalExe) -and (Test-Path -LiteralPath $previousExe)) {
        Copy-Item -LiteralPath $previousExe -Destination $canonicalExe
    }
    throw
}

Write-Host "EXE 已生成：$projectRoot\dist\$Name.exe"
if ($oldExes.Count -gt 0) { Write-Host "Previous versions archived: $backupDir" }
