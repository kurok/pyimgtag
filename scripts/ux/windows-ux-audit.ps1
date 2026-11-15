# Windows UX audit — runs the documented pyimgtag workflows on a Windows runner.
#
# Usage (on a Windows machine with the repo checked out):
#   pwsh -ExecutionPolicy Bypass -File scripts/ux/windows-ux-audit.ps1 -WorkDir C:\ux
#
# Everything is captured to a transcript so the output can be inspected after
# the fact (this is what the GitHub Actions workflow uploads as an artifact).
#
# NOTE: keep this file UTF-8 *with a BOM*. Windows PowerShell 5.1 reads a .ps1
# as ANSI without it, and the non-ASCII paths below then break the parser.

param(
    [string]$WorkDir = "C:\ux",
    [string]$OllamaUrl = "http://127.0.0.1:11435"
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Log = Join-Path $WorkDir "windows-ux-audit.log"
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

try {
    Start-Transcript -Path $Log -Force | Out-Null
} catch {
    Write-Output ("(transcript unavailable: " + $_.Exception.Message + ")")
}

Write-Section "ENVIRONMENT"
Invoke-Step "python" { python --version }
Invoke-Step "pip" { python -m pip --version }
Invoke-Step "pyimgtag version" { pyimgtag --version }
Invoke-Step "console code page" { cmd /c chcp }
Invoke-Step "exiftool present?" { exiftool -ver }
Invoke-Step "ps version" { $PSVersionTable.PSVersion.ToString() }
Write-Output "    cwd      : $((Get-Location).Path)"
Write-Output "    repoRoot : $RepoRoot"
Write-Output "    username : $env:USERNAME"

# ---------------------------------------------------------------------------
# Mock Ollama + fixtures
# ---------------------------------------------------------------------------
Write-Section "START MOCK OLLAMA"
$mock = Start-Process -FilePath "python" `
    -ArgumentList @("$RepoRoot\examples\mock_ollama.py", "11435") `
    -WorkingDirectory "$RepoRoot\examples" -PassThru `
    -RedirectStandardOutput (Join-Path $WorkDir "mock-ollama.log") `
    -RedirectStandardError (Join-Path $WorkDir "mock-ollama.err.log")
Start-Sleep -Seconds 3
$env:OLLAMA_URL = $OllamaUrl
$env:PYIMGTAG_NO_WEB = "1"
$env:PYIMGTAG_NO_UPDATE_CHECK = "1"

Write-Section "FIXTURES"
Invoke-Step "create fixtures" { python "$RepoRoot\examples\fixtures\create_fixtures.py" }
Invoke-Step "copy fixtures to work dir" { Copy-Item "$RepoRoot\examples\fixtures\*.jpg" $Photos -Force }

# A photo whose *file name* is non-ASCII, and a folder with a space in it —
# the two Windows-specific traps the README warns about.
$unicodeDir = Join-Path $WorkDir "Фото Архив"
$unicodeDir2 = Join-Path $WorkDir "Ünïcödé 日本"
New-Item -ItemType Directory -Force -Path $unicodeDir | Out-Null
New-Item -ItemType Directory -Force -Path $unicodeDir2 | Out-Null
Invoke-Step "copy a fixture into a non-ASCII folder" {
    Copy-Item "$Photos\sunset_beach.jpg" (Join-Path $unicodeDir "закат.jpg") -Force
}
Invoke-Step "copy a fixture into a second non-ASCII folder" {
    Copy-Item "$Photos\city_walk.jpg" (Join-Path $unicodeDir2 "café.png.jpg") -Force
}
Invoke-Step "exiftool reads the non-ASCII file" { exiftool -s -G1 (Join-Path $unicodeDir "закат.jpg") }

# ---------------------------------------------------------------------------
# Documented CLI workflows
# ---------------------------------------------------------------------------
Write-Section "PREFLIGHT"
Invoke-Step "preflight" { pyimgtag preflight --input-dir $Photos }
Invoke-Step "preflight (no input dir)" { pyimgtag preflight }

Write-Section "RUN"
Invoke-Step "run --dry-run" { pyimgtag run --input-dir $Photos --dry-run }
Invoke-Step "run (real, writes the DB)" { pyimgtag run --input-dir $Photos }
Invoke-Step "status" { pyimgtag status }
Invoke-Step "status --format json" { pyimgtag status --format json }
Invoke-Step "run --limit 2 --dry-run" { pyimgtag run --input-dir $Photos --limit 2 --dry-run }
Invoke-Step "run --jsonl-stdout" { pyimgtag run --input-dir $Photos --jsonl-stdout }
Invoke-Step "run --output-json / --output-csv" {
    pyimgtag run --input-dir $Photos --output-json (Join-Path $WorkDir "results.json")
    pyimgtag run --input-dir $Photos --output-csv (Join-Path $WorkDir "results.csv")
}
Invoke-Step "run --skip-existing" { pyimgtag run --input-dir $Photos --skip-existing }
Invoke-Step "run --resume-from-db" { pyimgtag run --input-dir $Photos --resume-from-db }
Invoke-Step "run -j 2" { pyimgtag run --input-dir $Photos -j 2 --dry-run }

Write-Section "RUN — EXIF WRITE-BACK"
$exifPhotos = Join-Path $WorkDir "photos_exif"
New-Item -ItemType Directory -Force -Path $exifPhotos | Out-Null
Copy-Item "$Photos\*.jpg" $exifPhotos -Force
Invoke-Step "run --write-exif" { pyimgtag run --input-dir $exifPhotos --write-exif }
Invoke-Step "exiftool reads back the written keywords" {
    exiftool -s -G1 -Keywords -Description (Join-Path $exifPhotos "sunset_beach.jpg")
}
Invoke-Step "run --write-exif on a NON-ASCII path" {
    pyimgtag run --input-dir $unicodeDir --write-exif
}
Invoke-Step "exiftool reads back the non-ASCII file" {
    exiftool -s -G1 -Keywords (Join-Path $unicodeDir "закат.jpg")
}
Invoke-Step "run --sidecar-only" { pyimgtag run --input-dir $Photos --sidecar-only --dry-run }

Write-Section "QUERY"
Invoke-Step "query --tag sunset" { pyimgtag query --tag sunset }
Invoke-Step "query --format json" { pyimgtag query --tag sunset --format json }
Invoke-Step "query --format paths" { pyimgtag query --tag sunset --format paths }
Invoke-Step "query --year" { pyimgtag query --year 2026 }
Invoke-Step "query --month" { pyimgtag query --month 2026-11 }
Invoke-Step "query --bbox (positive first value)" { pyimgtag query --bbox 37.70,-122.50,37.80,-122.30 }
Invoke-Step "query --bbox= (negative first value)" { pyimgtag query --bbox=-20,170,-10,-170 }
Invoke-Step "query --bbox (negative first value, space form)" { pyimgtag query --bbox -20,170,-10,-170 }
Invoke-Step "query --city" { pyimgtag query --city "San Francisco" }
Invoke-Step "query --has-text" { pyimgtag query --has-text }
Invoke-Step "query --scene-category" { pyimgtag query --scene-category outdoor_travel }
Invoke-Step "query on a NON-ASCII path" { pyimgtag query --tag beach }

Write-Section "TAGS"
Invoke-Step "tags list" { pyimgtag tags list }
Invoke-Step "tags list --format json" { pyimgtag tags list --format json }
Invoke-Step "tags rename" { pyimgtag tags rename sunset golden-hour }
Invoke-Step "tags merge" { pyimgtag tags merge golden-hour beach }
Invoke-Step "tags delete (unknown tag)" { pyimgtag tags delete does-not-exist }

Write-Section "CLEANUP / DRIFT / REPROCESS"
Invoke-Step "cleanup" { pyimgtag cleanup }
Invoke-Step "cleanup --include-review" { pyimgtag cleanup --include-review }
Invoke-Step "cleanup --format json" { pyimgtag cleanup --format json }
Invoke-Step "cleanup-drift" { pyimgtag cleanup-drift }
Invoke-Step "reprocess (no --yes)" { pyimgtag reprocess }

Write-Section "JUDGE"
Invoke-Step "judge" { pyimgtag judge --input-dir $Photos }
Invoke-Step "judge --min-score 9 (nothing passes)" { pyimgtag judge --input-dir $Photos --min-score 9 }
Invoke-Step "judge -v" { pyimgtag judge --input-dir $Photos -v }
Invoke-Step "judge --output-json" { pyimgtag judge --input-dir $Photos --output-json (Join-Path $WorkDir "ranking.json") }

Write-Section "DEDUP"
Invoke-Step "dedup scan" { pyimgtag dedup scan }
Invoke-Step "dedup list" { pyimgtag dedup list }
Invoke-Step "dedup resolve (report only)" { pyimgtag dedup resolve }
Invoke-Step "dedup resolve --move-to" { pyimgtag dedup resolve --move-to (Join-Path $WorkDir "quarantine") }
Invoke-Step "dedup undo" { pyimgtag dedup undo }

Write-Section "INSIGHTS / EXPORT / EVENTS"
Invoke-Step "insights" { pyimgtag insights }
Invoke-Step "insights --format json" { pyimgtag insights --format json }
Invoke-Step "insights --output html" { pyimgtag insights --output (Join-Path $WorkDir "report.html") }
Invoke-Step "export csv" { pyimgtag export --format csv --output (Join-Path $WorkDir "library.csv") }
Invoke-Step "export json" { pyimgtag export --format json --output (Join-Path $WorkDir "library.json") }
Invoke-Step "export digikam" { pyimgtag export --format digikam --output (Join-Path $WorkDir "tags.xml") }
Invoke-Step "events detect" { pyimgtag events detect }
Invoke-Step "events list" { pyimgtag events list }
Invoke-Step "events show 1" { pyimgtag events show 1 }
Invoke-Step "events apply --export-dir" { pyimgtag events apply --export-dir (Join-Path $WorkDir "albums") }

Write-Section "FACES (optional extra, error path)"
Invoke-Step "faces scan without the [face] extra" { pyimgtag faces scan --input-dir $Photos }
Invoke-Step "faces --help" { pyimgtag faces --help }

Write-Section "SEMANTIC SEARCH"
Invoke-Step "index" { pyimgtag index --input-dir $Photos }
Invoke-Step "search" { pyimgtag search "sunset over the ocean" }
Invoke-Step "search --similar-to" { pyimgtag search --similar-to (Join-Path $Photos "sunset_beach.jpg") }

Write-Section "CONTROLLED VOCABULARY / PROMPT"
Invoke-Step "run --vocabulary (yaml)" {
    pyimgtag run --input-dir $Photos --vocabulary "$RepoRoot\examples\vocabularies\birding.yaml" --dry-run
}
Invoke-Step "run --vocabulary (json)" {
    pyimgtag run --input-dir $Photos --vocabulary "$RepoRoot\examples\vocabularies\birding.json" --dry-run
}
Invoke-Step "prompt show" { pyimgtag prompt show }

Write-Section "MCP"
Invoke-Step "mcp initialize + tools/list" {
    $reqs = @(
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"audit","version":"1"}}}',
        '{"jsonrpc":"2.0","method":"notifications/initialized"}',
        '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
    )
    $reqs | pyimgtag mcp
}

Write-Section "WATCH"
Invoke-Step "watch --max-cycles 1" { pyimgtag watch --input-dir $Photos --interval 5 --max-cycles 1 --no-web }
Invoke-Step "watch --install-service (prints the Windows recipe)" {
    pyimgtag watch --input-dir $Photos --interval 600 --install-service
}

Write-Section "WEBAPP"
Invoke-Step "start the unified app and probe every page" {
    $web = Start-Process -FilePath "python" -ArgumentList @("-m", "pyimgtag.webapp") `
        -WorkingDirectory $RepoRoot -PassThru `
        -RedirectStandardOutput (Join-Path $WorkDir "webapp.log") `
        -RedirectStandardError (Join-Path $WorkDir "webapp.err.log")
    $env:PORT = "8770"
    Start-Sleep -Seconds 8
    foreach ($p in @("/health", "/", "/review/", "/query/", "/tags/", "/insights/", "/map/", "/timeline/", "/dedup/", "/judge/", "/faces/", "/about/", "/edit/")) {
        try {
            $resp = Invoke-WebRequest -Uri ("http://127.0.0.1:8770" + $p) -UseBasicParsing -TimeoutSec 20
            Write-Output ("    {0,-14} -> {1} ({2} bytes)" -f $p, $resp.StatusCode, $resp.RawContentLength)
        } catch {
            Write-Output ("    {0,-14} -> ERROR {1}" -f $p, $_.Exception.Message)
        }
    }
    Stop-Process -Id $web.Id -Force -ErrorAction SilentlyContinue
}

Write-Section "DONE"
Write-Output "Audit finished."
try { Stop-Transcript | Out-Null } catch { }
