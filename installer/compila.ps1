# Costruisce VideoWatermarkRemover-Setup.exe su Windows (PowerShell).
#
# Serve: uv, makensis (NSIS), curl/tar (inclusi in Windows 10+).
#
#   .\installer\compila.ps1
#   $env:BUILD = "D:\wmr-build"; .\installer\compila.ps1

$ErrorActionPreference = "Stop"

$Qui = $PSScriptRoot
$Progetto = Split-Path -Parent $Qui
$Build = if ($env:BUILD) { $env:BUILD } else { Join-Path $Qui "build" }
$Uscita = if ($env:USCITA) { $env:USCITA } else { Join-Path $Progetto "VideoWatermarkRemover-Setup.exe" }

$VersionePython = "3.12.13"
$RilascioPython = "20260718"
$Archivio = "cpython-${VersionePython}+${RilascioPython}-x86_64-pc-windows-msvc-install_only.tar.gz"
$UrlPython = "https://github.com/astral-sh/python-build-standalone/releases/download/${RilascioPython}/${Archivio}"

$Makensis = @(
    "${env:ProgramFiles}\NSIS\makensis.exe",
    "${env:ProgramFiles(x86)}\NSIS\makensis.exe",
    "makensis"
) | ForEach-Object {
    if ($_ -eq "makensis") {
        $cmd = Get-Command makensis -ErrorAction SilentlyContinue
        if ($cmd) { $cmd.Source }
    } elseif (Test-Path $_) { $_ }
} | Select-Object -First 1

if (-not $Makensis) {
    throw "makensis non trovato. Installa NSIS: winget install NSIS.NSIS"
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv non trovato. Installa uv: https://docs.astral.sh/uv/"
}

Write-Host "progetto:  $Progetto"
Write-Host "lavoro in: $Build"
Write-Host "risultato: $Uscita"
New-Item -ItemType Directory -Force -Path $Build | Out-Null

$PythonBuild = Join-Path $Build "python\python.exe"
if (-not (Test-Path $PythonBuild)) {
    Write-Host ""
    Write-Host "== Scarico Python $VersionePython per Windows =="
    $ArchivioPath = Join-Path $Build $Archivio
    if (-not (Test-Path $ArchivioPath)) {
        curl.exe -L --progress-bar -o $ArchivioPath $UrlPython
        if ($LASTEXITCODE -ne 0) { throw "download Python fallito" }
    }
    $PythonDir = Join-Path $Build "python"
    if (Test-Path $PythonDir) { Remove-Item -Recurse -Force $PythonDir }
    tar.exe -xzf $ArchivioPath -C $Build
    if ($LASTEXITCODE -ne 0) { throw "estrazione Python fallita" }

    Write-Host "== Tolgo cio' che non serve a far girare il programma =="
    Push-Location $PythonDir
    try {
        Get-ChildItem -Recurse -Filter "*.pdb" -ErrorAction SilentlyContinue | Remove-Item -Force
        @(
            "include", "libs", "Lib\idlelib", "Lib\turtledemo",
            "Lib\ensurepip", "Lib\test"
        ) | ForEach-Object {
            $p = Join-Path $PythonDir $_
            if (Test-Path $p) { Remove-Item -Recurse -Force $p }
        }
        Get-ChildItem -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    } finally {
        Pop-Location
    }

    Write-Host "== Aggiungo le librerie =="
    & uv pip install --python $PythonBuild `
        "opencv-python-headless>=4.8,<5" numpy imageio-ffmpeg tqdm
    if ($LASTEXITCODE -ne 0) { throw "uv pip install fallito" }
} else {
    Write-Host "runtime Python gia' pronto (cancella $Build\python per rifarlo)"
}

Write-Host ""
Write-Host "== Preparo i file del programma =="
$Icona = Join-Path $Qui "icona.ico"
if (-not (Test-Path $Icona)) {
    $PyLocale = Join-Path $Progetto ".venv\Scripts\python.exe"
    if (-not (Test-Path $PyLocale)) { $PyLocale = "python" }
    & $PyLocale (Join-Path $Qui "crea_icona.py") $Icona
    if ($LASTEXITCODE -ne 0) { throw "crea_icona.py fallito" }
}

$App = Join-Path $Build "app"
if (Test-Path $App) { Remove-Item -Recurse -Force $App }
New-Item -ItemType Directory -Force -Path $App | Out-Null
Copy-Item (Join-Path $Progetto "dewatermark.py") $App
Copy-Item (Join-Path $Progetto "interfaccia.py") $App
Copy-Item (Join-Path $Progetto "README.md") $App
Copy-Item $Icona $App
Copy-Item (Join-Path $Progetto "wmremove") $App -Recurse
Copy-Item (Join-Path $Progetto "scripts") $App -Recurse
Get-ChildItem $App -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $App -Recurse -Filter "._*" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

# Controllo anti-regressione: il backend LaMa non deve richiamare iopaint.
$Lama = Join-Path $App "wmremove\backends\lama.py"
$LamaText = Get-Content $Lama -Raw
if ($LamaText -match '(?m)^\s*"iopaint"|pip install iopaint|shutil\.which\("iopaint"\)') {
    throw "lama.py nel pacchetto richiama ancora iopaint: interrompo la build"
}
if ($LamaText -notmatch "torch\.jit\.load") {
    throw "lama.py non contiene il caricamento TorchScript atteso"
}

Write-Host ""
Write-Host "== Compilo l'installer =="
# makensis risolve i File relativi alla cartella dello script .nsi.
# Con un BUILD assoluto su Windows spesso non trova i file: usiamo un
# percorso relativo quando la build sta sotto installer/.
$BuildRel = $Build
$QuiFull = (Resolve-Path $Qui).Path
$BuildFull = (Resolve-Path $Build).Path
if ($BuildFull.StartsWith($QuiFull, [System.StringComparison]::OrdinalIgnoreCase)) {
    $BuildRel = $BuildFull.Substring($QuiFull.Length).TrimStart('\', '/')
    if (-not $BuildRel) { $BuildRel = "." }
}
$BuildNsis = $BuildRel
# OutFile accetta anche /; i File /r su Windows no, restano col separatore nativo.
$UscitaNsis = ($Uscita -replace '\\', '/')
Write-Host "BUILD nsis:  $BuildNsis"
Write-Host "USCITA nsis: $UscitaNsis"
Push-Location $Qui
try {
    & $Makensis /V2 "/DBUILD=$BuildNsis" "/DUSCITA=$UscitaNsis" ".\installer.nsi"
    if ($LASTEXITCODE -ne 0) { throw "makensis fallito (codice $LASTEXITCODE)" }
} finally {
    Pop-Location
}

Get-Item $Uscita | Format-List FullName, Length, LastWriteTime
Write-Host "fatto."
