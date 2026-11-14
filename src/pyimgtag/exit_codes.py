"""Process exit codes shared by ``run`` and ``judge``.

| Code | Meaning |
|------|---------|
| 0 | at least one item succeeded, or nothing needed processing |
| 1 | interrupted, or a setup/usage error detected by the command |
| 2 | argparse usage error |
| 3 | at least one item was attempted and every attempt failed at the model |
"""

from __future__ import annotations

#: Every item that reached the model failed there.
EXIT_ALL_MODEL_CALLS_FAILED = 3


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
