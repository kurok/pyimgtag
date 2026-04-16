# pyimgtag Examples

Runnable shell scripts covering every major use case. Each script uses `--dry-run` by default where applicable. See the [pyimgtag Wiki](https://github.com/kurok/pyimgtag/wiki) for detailed explanations and real output.

## Scripts

| Script | Description | Wiki page |
|---|---|---|
| `01_basic_run.sh` | Dry-run on an exported folder (first 20 images) | [Tagging Your Photos](https://github.com/kurok/pyimgtag/wiki/Tagging-Your-Photos) |
| `02_date_range_export.sh` | Date range filter with JSON + CSV output | [Tagging Your Photos](https://github.com/kurok/pyimgtag/wiki/Tagging-Your-Photos) |
| `03_photos_library.sh` | Apple Photos library scan + write-back (macOS only) | [Tagging Your Photos](https://github.com/kurok/pyimgtag/wiki/Tagging-Your-Photos) |
| `04_linux_windows.sh` | Cross-platform exported folder with EXIF write | [Advanced Topics](https://github.com/kurok/pyimgtag/wiki/Advanced-Topics) |
| `05_query_and_review.sh` | Query filters + web review UI | [Reviewing Results](https://github.com/kurok/pyimgtag/wiki/Reviewing-Results) |
| `06_cleanup_candidates.sh` | List delete/review flagged photos | [Managing Your Library](https://github.com/kurok/pyimgtag/wiki/Managing-Your-Library) |
| `07_tag_management.sh` | Tags list / rename / delete / merge | [Managing Your Library](https://github.com/kurok/pyimgtag/wiki/Managing-Your-Library) |
| `08_faces_workflow.sh` | Face scan → cluster → review → apply | [Face Recognition](https://github.com/kurok/pyimgtag/wiki/Face-Recognition) |
| `09_raw_heic.sh` | RAW (CR2/NEF/ARW/DNG) + HEIC tagging | [Advanced Topics](https://github.com/kurok/pyimgtag/wiki/Advanced-Topics) |
| `10_dedup_and_reprocess.sh` | Perceptual dedup + reprocess after model change | [Managing Your Library](https://github.com/kurok/pyimgtag/wiki/Managing-Your-Library) |

## Mock Ollama

To run examples without a live Ollama instance, use the included mock server:

```bash
python3 mock_ollama.py 11435 &
export OLLAMA_URL=http://127.0.0.1:11435
./01_basic_run.sh ~/Pictures/exported
```

## Generating Demo Output

To regenerate the captured output in `captured/`:

```bash
python3 fixtures/create_fixtures.py   # create test images
python3 capture_demo.py               # run all commands and save output
```
