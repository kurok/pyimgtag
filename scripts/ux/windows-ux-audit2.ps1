# Windows UX audit, part 2 — the Windows-specific checks that need a real Windows.
#
# Companion to windows-ux-audit.ps1: long paths, non-ASCII paths, the EXIF
# round-trip, and the webapp pages.

param(
    [string]$WorkDir = "C:\ux2"
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Log = Join-Path $WorkDir "windows-ux-audit2.log"
$Photos = Join-Path $WorkDir "photos"

function Write-Section($title) {
    Write-Output ""
    Write-Output ("=" * 78)
    Write-Output "== $title"
    Write-Output ("=" * 78)
}

function Invoke-Step {
    param([string]$Title, [scriptblock]$Command)
    Write-Output ""
    Write-Output "--- $Title"
    Write-Output ("$ " + $Command.ToString().Trim())
    $global:LASTEXITCODE = 0
    try {
        & $Command 2>&1 | ForEach-Object { Write-Output ("    " + $_) }
    } catch {
        Write-Output ("    EXCEPTION: " + $_.Exception.Message)
    }
    Write-Output "    EXIT=$LASTEXITCODE"
}

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
New-Item -ItemType Directory -Force -Path $Photos | Out-Null
try { Start-Transcript -Path $Log -Force | Out-Null } catch { }

$env:OLLAMA_URL = "http://127.0.0.1:11435"
$env:PYIMGTAG_NO_WEB = "1"
$env:PYIMGTAG_NO_UPDATE_CHECK = "1"

Write-Section "ENVIRONMENT"
Invoke-Step "os" { (Get-CimInstance Win32_OperatingSystem).Caption + " " + [System.Environment]::OSVersion.Version }
Invoke-Step "long paths enabled?" { Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" | Select-Object -ExpandProperty LongPathsEnabled }
Invoke-Step "python" { python --version }
Invoke-Step "exiftool" { exiftool -ver }

Write-Section "START MOCK OLLAMA"
$mock = Start-Process -FilePath "python" -ArgumentList @("$RepoRoot\examples\mock_ollama.py", "11435") `
    -WorkingDirectory "$RepoRoot\examples" -PassThru `
    -RedirectStandardOutput (Join-Path $WorkDir "mock-ollama.log") `
    -RedirectStandardError (Join-Path $WorkDir "mock-ollama.err.log")
Start-Sleep -Seconds 3
Invoke-Step "mock ollama reachable" { (Invoke-WebRequest -Uri "http://127.0.0.1:11435/api/tags" -UseBasicParsing).StatusCode }

Write-Section "FIXTURES"
Invoke-Step "create fixtures" { python "$RepoRoot\examples\fixtures\create_fixtures.py" }
Invoke-Step "copy fixtures" { Copy-Item "$RepoRoot\examples\fixtures\*.jpg" $Photos -Force }

# --- a deep path, past the classic 260-character MAX_PATH ------------------
$deep = $Photos
for ($i = 0; $i -lt 12; $i++) {
    $deep = Join-Path $deep ("level-with-a-fairly-long-name-$i")
}
Invoke-Step "create a deep folder" { New-Item -ItemType Directory -Force -Path $deep | Out-Null }
Invoke-Step "copy a fixture into the deep folder" { Copy-Item "$Photos\sunset_beach.jpg" (Join-Path $deep "deep.jpg") -Force }
Invoke-Step "path length" { (Join-Path $deep "deep.jpg").Length }
Invoke-Step "run over the deep path" { pyimgtag run --input-dir $deep --dry-run }
Invoke-Step "python can open the deep file" { python -c "import sys;from PIL import Image;im=Image.open(sys.argv[1]);print('opened', im.size)" (Join-Path $deep "deep.jpg") }

# --- a non-ASCII path ------------------------------------------------------
$unicodeDir = Join-Path $WorkDir "Фото Архив"
New-Item -ItemType Directory -Force -Path $unicodeDir | Out-Null
Invoke-Step "copy a fixture into a non-ASCII folder" { Copy-Item "$Photos\sunset_beach.jpg" (Join-Path $unicodeDir "закат.jpg") -Force }
Invoke-Step "exiftool reads the non-ASCII file (argv)" { exiftool -s -G1 -Keywords (Join-Path $unicodeDir "закат.jpg") }
Invoke-Step "exiftool reads the non-ASCII file (argfile, as pyimgtag does)" {
    $argfile = Join-Path $WorkDir "args.txt"
    "-charset`nUTF8`n-Keywords`n" + (Join-Path $unicodeDir "закат.jpg") | Set-Content -Path $argfile -Encoding UTF8
    exiftool -@ $argfile
}
Invoke-Step "run over the non-ASCII path" { pyimgtag run --input-dir $unicodeDir --write-exif }
Invoke-Step "read the written keywords back with Pillow" {
    python -c "import sys;from PIL import Image;im=Image.open(sys.argv[1]);ex=im.getexif();print('xmp/iptc written:', bool(ex))" (Join-Path $unicodeDir "закат.jpg")
}
Invoke-Step "read the file back with exiftool -@ argfile" {
    $argfile = Join-Path $WorkDir "args2.txt"
    "-charset`nUTF8`n-a`n-G1`n-Keywords`n-Description`n" + (Join-Path $unicodeDir "закат.jpg") | Set-Content -Path $argfile -Encoding UTF8
    exiftool -@ $argfile
}

# --- case-insensitive paths ------------------------------------------------
Write-Section "CASE"
Invoke-Step "query with a differently-cased path is irrelevant here, but a path with a trailing space is not" {
    pyimgtag query --tag beach --format paths
}

# --- the webapp ------------------------------------------------------------
Write-Section "WEBAPP"
Invoke-Step "start the unified app (python -m pyimgtag.webapp) and probe every page" {
    $env:PORT = "8770"
    $env:HOST = "127.0.0.1"
    $web = Start-Process -FilePath "python" -ArgumentList @("-m", "pyimgtag.webapp") `
        -WorkingDirectory $RepoRoot -PassThru `
        -RedirectStandardOutput (Join-Path $WorkDir "webapp.log") `
        -RedirectStandardError (Join-Path $WorkDir "webapp.err.log")
    Start-Sleep -Seconds 10
    foreach ($port in @(8770, 8000)) {
        Write-Output ("    -- port $port --")
        foreach ($p in @("/health", "/", "/review/", "/query/", "/tags/", "/insights/", "/map/", "/timeline/", "/dedup/", "/judge/", "/faces/", "/about/", "/edit/")) {
            try {
                $resp = Invoke-WebRequest -Uri ("http://127.0.0.1:$port" + $p) -UseBasicParsing -TimeoutSec 20
                Write-Output ("    {0,-14} -> {1} ({2} bytes)" -f $p, $resp.StatusCode, $resp.RawContentLength)
            } catch {
                Write-Output ("    {0,-14} -> ERROR {1}" -f $p, $_.Exception.Message)
            }
        }
    }
    Stop-Process -Id $web.Id -Force -ErrorAction SilentlyContinue
}
Invoke-Step "webapp stderr" { Get-Content (Join-Path $WorkDir "webapp.err.log") -ErrorAction SilentlyContinue | Select-Object -Last 20 }

Write-Section "DONE"
Write-Output "Audit finished."
try { Stop-Transcript | Out-Null } catch { }
