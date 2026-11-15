# UX audits

Two ways to exercise the **documented** workflows end to end and read the
transcript: a realistic-corpus run on Linux/macOS, and a Windows-specific run.

Two PowerShell scripts that walk the **documented** pyimgtag workflows on a
real Windows machine and write a transcript, so a UX regression on Windows can
be seen instead of guessed at.

They need no model: the repo's own `examples/mock_ollama.py` stands in for
Ollama, and `examples/fixtures/create_fixtures.py` produces the photos.

| Script | What it covers | Typical runtime |
|---|---|---|
| `windows-ux-audit.ps1` | Every documented command: `run` (dry-run, filters, limits, resume, `-j`, `--write-exif`, `--sidecar-only`), `status`, `reprocess`, `cleanup`, `cleanup-drift`, `query` (every filter), `tags`, `judge`, `dedup` (scan/list/resolve/undo), `insights`, `events`, `export`, `index`/`search`, vocabulary, `prompt`, `mcp`, `watch`, and every webapp page | ~25 min (most of it geocoding) |
| `windows-ux-audit2.ps1` | The Windows-specific traps: a 400-character path, a non-ASCII folder *and* file name, the EXIF round-trip on a non-ASCII path, the non-ASCII *metadata* round-trip, and the webapp pages | ~3 min |

## Running them

On a Windows machine with the repo checked out:

```powershell
pip install -e ".[heic,review,dedup,vocab,mcp]" piexif
choco install exiftool          # or: winget install OliverBetz.ExifTool
pwsh -ExecutionPolicy Bypass -File scripts/ux/windows-ux-audit.ps1  -WorkDir C:\ux
pwsh -ExecutionPolicy Bypass -File scripts/ux/windows-ux-audit2.ps1 -WorkDir C:\ux2
```

Each script writes its transcript next to the work directory
(`<WorkDir>\windows-ux-audit*.log`) and prints it to stdout.

## Running them in CI

The *Windows UX audit* workflow (`.github/workflows/windows-ux-audit.yml`) runs
them on `windows-latest` and uploads the transcript as an artifact:

```bash
gh workflow run windows-ux-audit.yml -f part=2      # 1 = every command, 2 = Windows traps, all = both
gh run list --workflow windows-ux-audit.yml
gh run download <run-id> --name windows-ux-audit-log
```

## Notes for anyone editing these

- The scripts are saved **UTF-8 with a BOM**. Windows PowerShell 5.1 reads a
  `.ps1` file as ANSI when there is no BOM, and the non-ASCII paths in the
  script then break the parser. `pwsh` (PowerShell 7) does not need the BOM,
  but keeping it means the scripts also run under `powershell`.
- Do **not** pipe the script's output through `Tee-Object` in CI: a child
  process that inherits the pipe handle (the mock Ollama server, the webapp)
  keeps the pipe open and the step runs until the job times out. Redirect to a
  file (`*> audit.out`) instead — that is what the workflow does.
- Every step is wrapped so that one failing command does not abort the whole
  transcript.

## Realistic user journeys (Linux / macOS)

`make_realistic_corpus.py` builds a library that looks like a real one, and
`mock_ollama_realistic.py` is a mock that *looks at the pixels* instead of
replaying a fixed list — so the corpus behaves like a real library (dark shots
come back as cleanup candidates, the burst frames score alike, `judge` ranks
by something other than a constant).

```bash
pip install -e ".[heic,review,dedup,vocab,mcp]" piexif
apt-get install -y exiftool ffmpeg          # or: brew install exiftool ffmpeg

python scripts/ux/make_realistic_corpus.py            # -> /lab/library
python scripts/ux/mock_ollama_realistic.py 11435 &     # stand-in for Ollama
export OLLAMA_URL=http://127.0.0.1:11435
bash scripts/ux/real_user_journeys.sh                  # -> /lab/out/journeys.log
```

The corpus is 46 files: phone JPEGs with EXIF (dates over four months, GPS in
five cities), a four-frame burst, two exact duplicates, screenshots without
EXIF, a HEIC photo, two video clips, a broken `.CR2`, a `.txt`, a `.DS_Store`,
and names with spaces, accents and non-Latin scripts.

`real_user_journeys.sh` then walks sixteen journeys over it — first import,
"only my July holiday", "where was that photo", "what should I throw away",
"score them", "clean up the burst", "group my trip into albums", "what is in
my library", "give it to my photo manager", "write the tags into the files",
"tag new photos as they arrive", the MCP tools, the awkward files, a library
without EXIF, videos and tag management — and writes everything to
`/lab/out/journeys.log`.

The *Realistic UX journeys* workflow runs the same thing on `ubuntu-latest`
and uploads the transcript:

```bash
gh workflow run realistic-ux-journeys.yml
gh run download <run-id> --name realistic-ux-journeys-log
```
