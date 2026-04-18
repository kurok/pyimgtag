# Platform Setup Guide

pyimgtag core features (AI tagging, geocoding, HEIC/RAW conversion, duplicate detection, JSON/CSV export) work on all platforms. Apple Photos integration (library scanning, keyword write-back, face management) is macOS-only.

---

## macOS

### Prerequisites

- Python 3.11+ (system Python or [pyenv](https://github.com/pyenv/pyenv))
- [Homebrew](https://brew.sh) for system dependencies:

```bash
brew install ollama exiftool
ollama pull gemma4:e4b
```

### Installation

```bash
pip install "pyimgtag[all]"
```

Or from source:

```bash
git clone https://github.com/kurok/pyimgtag.git
cd pyimgtag
pip install -e ".[all,dev]"
```

### Available Features

| Feature | Available |
|---|---|
| AI tagging via Ollama/Gemma | Yes |
| EXIF GPS reverse geocoding | Yes |
| HEIC conversion (native via `sips`) | Yes |
| HEIC conversion (via pillow-heif) | Yes |
| RAW conversion (via rawpy) | Yes (`[raw]` extra) |
| Apple Photos library scanning | Yes (macOS only) |
| Apple Photos keyword write-back | Yes (macOS only) |
| Apple Photos face import | Yes (macOS only) |
| JSON/CSV/JSONL export | Yes |
| Duplicate detection | Yes |
| Photo quality scoring (`judge`) | Yes |

### Common Use Cases

**Tag Photos library with write-back:**

```bash
pyimgtag run --write-back
```

**Score photos with judge:**

```bash
pyimgtag judge ~/Pictures --min-score 3.5
```

**Import faces from Apple Photos:**

```bash
pyimgtag faces --import
```

**Process exported HEIC photos:**

```bash
pyimgtag run ~/Desktop/export --output results.json
```

### Permissions

Apple Photos library access requires Full Disk Access:

1. Open **System Settings → Privacy & Security → Full Disk Access**
2. Enable the terminal application you use (Terminal, iTerm2, or your IDE's terminal)
3. Restart the terminal after granting access

### Troubleshooting

| Symptom | Fix |
|---|---|
| `Operation not permitted` when accessing Photos | Grant Full Disk Access (see Permissions above) |
| `exiftool not found` | `brew install exiftool` |
| HEIC files not loading | `pip install pillow-heif` |
| `Ollama not running` or connection refused | `brew services start ollama` or `ollama serve` |
| `Model not found` | `ollama pull gemma4:e4b` |

---

## Linux

### Prerequisites

- Python 3.11+ (distro packages or [pyenv](https://github.com/pyenv/pyenv))
- exiftool:

    **Ubuntu/Debian:**
    ```bash
    sudo apt-get install libimage-exiftool-perl
    ```

    **Fedora/RHEL:**
    ```bash
    sudo dnf install perl-Image-ExifTool
    ```

    **Arch:**
    ```bash
    sudo pacman -S perl-image-exiftool
    ```

- Ollama:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma4:e4b
```

### Installation

```bash
pip install "pyimgtag[heic]"
```

Or from source:

```bash
git clone https://github.com/kurok/pyimgtag.git
cd pyimgtag
pip install -e ".[heic,dev]"
```

For RAW support, add the `[raw]` extra:

```bash
pip install "pyimgtag[heic,raw]"
```

### Available Features

| Feature | Available |
|---|---|
| AI tagging via Ollama/Gemma | Yes |
| EXIF GPS reverse geocoding | Yes |
| HEIC conversion (via pillow-heif) | Yes (`[heic]` extra) |
| RAW conversion (via rawpy) | Yes (`[raw]` extra) |
| Apple Photos library scanning | No |
| Apple Photos keyword write-back | No |
| Apple Photos face import | No |
| JSON/CSV/JSONL export | Yes |
| Duplicate detection | Yes |
| Photo quality scoring (`judge`) | Yes |

### Common Use Cases

**Tag an exported folder:**

```bash
pyimgtag run ~/Pictures/export --output results.json
```

**Score photos:**

```bash
pyimgtag judge ~/Pictures/export --min-score 4.0
```

**Write EXIF tags back to files:**

```bash
pyimgtag run ~/Pictures/export --write-back
```

**Export results to JSON:**

```bash
pyimgtag run ~/Pictures/export --output results.json --format json
```

### Troubleshooting

| Symptom | Fix |
|---|---|
| `exiftool not found` | Install via your distro package manager (see Prerequisites) |
| HEIC files not loading | `pip install pillow-heif` |
| `Ollama not running` or connection refused | `ollama serve` (or set up as a systemd service) |
| `Model not found` | `ollama pull gemma4:e4b` |
| Permission denied on photo directory | Check directory ownership: `ls -la ~/Pictures` |

---

## Windows

### Prerequisites

- Python 3.11+ from [python.org](https://www.python.org/downloads/) — check **"Add Python to PATH"** during installation
- Ollama from [ollama.com](https://ollama.com/download)

    After installing, pull the model:
    ```powershell
    ollama pull gemma4:e4b
    ```

- exiftool — choose one method:
    - **Chocolatey:** `choco install exiftool`
    - **winget:** `winget install OliverBetz.ExifTool`
    - **Manual:** download from [exiftool.org](https://exiftool.org), extract `exiftool(-k).exe`, rename to `exiftool.exe`, and place in a directory on your `PATH`

### Installation

```powershell
pip install "pyimgtag[heic]"
```

Or from source:

```powershell
git clone https://github.com/kurok/pyimgtag.git
cd pyimgtag
pip install -e ".[heic,dev]"
```

For RAW support:

```powershell
pip install "pyimgtag[heic,raw]"
```

### Available Features

| Feature | Available |
|---|---|
| AI tagging via Ollama/Gemma | Yes |
| EXIF GPS reverse geocoding | Yes |
| HEIC conversion (via pillow-heif) | Yes (`[heic]` extra) |
| RAW conversion (via rawpy) | Yes (`[raw]` extra) |
| Apple Photos library scanning | No |
| Apple Photos keyword write-back | No |
| Apple Photos face import | No |
| JSON/CSV/JSONL export | Yes |
| Duplicate detection | Yes |
| Photo quality scoring (`judge`) | Yes |

### Common Use Cases

**Tag photos in a Windows path (use quotes for paths with spaces):**

```powershell
pyimgtag run "C:\Users\YourName\Pictures\Vacation" --output results.json
```

**Score photos:**

```powershell
pyimgtag judge "C:\Users\YourName\Pictures" --min-score 3.5
```

**Output JSON for import into other tools:**

```powershell
pyimgtag run "C:\Users\YourName\Pictures" --output results.json --format json
```

### Troubleshooting

| Symptom | Fix |
|---|---|
| `exiftool is not recognized` | Add exiftool directory to `PATH` in System Environment Variables, or use the full path |
| `python is not recognized` | Reinstall Python from python.org and check "Add Python to PATH" |
| Long path errors | Enable long paths: run `regedit`, navigate to `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem`, set `LongPathsEnabled` to `1`; or via Group Policy: **Local Computer Policy → Computer Configuration → Administrative Templates → System → Filesystem → Enable Win32 long paths** |
| Ollama not running | Start from the system tray icon or run `ollama serve` in a terminal |
| `Model not found` | `ollama pull gemma4:e4b` |

---

## Feature Availability by Platform

| Feature | macOS | Linux | Windows |
|---|:---:|:---:|:---:|
| AI tagging (Ollama/Gemma) | Yes | Yes | Yes |
| EXIF GPS reverse geocoding | Yes | Yes | Yes |
| HEIC support (native `sips`) | Yes | No | No |
| HEIC support (pillow-heif) | Yes | Yes | Yes |
| RAW support (rawpy) | Yes | Yes | Yes |
| JSON/CSV/JSONL export | Yes | Yes | Yes |
| Duplicate detection | Yes | Yes | Yes |
| Photo quality scoring (`judge`) | Yes | Yes | Yes |
| Apple Photos library scanning | Yes | No | No |
| Apple Photos keyword write-back | Yes | No | No |
| Apple Photos face import (`faces`) | Yes | No | No |

**Install extras reference:**

| Extra | Installs | Use for |
|---|---|---|
| `[heic]` | pillow-heif | HEIC support on Linux/Windows |
| `[raw]` | rawpy | RAW file support (all platforms) |
| `[all]` | pillow-heif + rawpy | All optional format support |
| `[dev]` | pytest, coverage, etc. | Development and testing |
