"""Process exit codes shared by ``run`` and ``judge``.

| Code | Meaning |
|------|---------|
| 0 | at least one item succeeded, or nothing needed processing |
| 1 | interrupted, or a setup/usage error detected by the command |
| 2 | argparse usage error |
| 3 | at least one item was attempted and every attempt failed at the model |
| 4 | the model calls worked, but every metadata write that was asked for failed |
"""

from __future__ import annotations

#: Every item that reached the model failed there.
EXIT_ALL_MODEL_CALLS_FAILED = 3

#: Tagging worked, but every requested metadata write (EXIF, sidecar, Photos)
#: failed: nothing the user asked to be written was written.
EXIT_ALL_WRITES_FAILED = 4


def exit_code_for(attempted: int, failed: int) -> int:
    """Return the exit code for a finished, uninterrupted ``run`` or ``judge``.

    Args:
        attempted: Items that reached the model.
        failed: Of those, items whose model call failed.

    Returns:
        ``3`` when at least one item was attempted and every attempt failed,
        so cron jobs and CI can tell a dead run from a quiet one; otherwise
        ``0``. Interruption (``1``) is decided by the caller first.
    """
    if attempted > 0 and failed >= attempted:
        return EXIT_ALL_MODEL_CALLS_FAILED
    return 0


def run_exit_code(stats: dict[str, int]) -> int:
    """Return the exit code for a finished, uninterrupted ``run`` from its stats.

    A dead model wins (``3``). Otherwise, when at least one metadata write was
    attempted and none succeeded, ``4``: a run that tagged everything but wrote
    nothing must not look like a success to cron or a ``watch`` service. Any
    successful write, or no write attempted (no write flag, ``--dry-run``),
    leaves it to the model result.
    """
    code = exit_code_for(stats.get("model_calls", 0), stats.get("model_failures", 0))
    if code:
        return code
    written = (
        stats.get("metadata_written", 0)
        + stats.get("sidecars_written", 0)
        + stats.get("written_back", 0)
    )
    failed = stats.get("metadata_write_failures", 0) + stats.get("write_back_failures", 0)
    if failed and not written:
        return EXIT_ALL_WRITES_FAILED
    return 0
