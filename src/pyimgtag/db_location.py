"""Where the progress database lives: ``--db``, then ``PYIMGTAG_DB``, then the default.

Kept import-free so ``main.py`` can resolve the path before loading any
command. ``python -m pyimgtag.webapp`` uses the same resolver.
"""

from __future__ import annotations

import os

#: Environment variable naming the progress database when ``--db`` is not given.
DB_ENV = "PYIMGTAG_DB"


def resolve_db_path(cli_value: str | None) -> str | None:
    """Return the database path to use, or ``None`` for the default location.

    Precedence: an explicit ``--db`` value, then a non-empty ``PYIMGTAG_DB``,
    then ``None`` (``ProgressDB`` then picks ``~/.cache/pyimgtag/progress.db``).
    An empty ``PYIMGTAG_DB`` counts as unset.
    """
    if cli_value is not None:
        return cli_value
    return os.environ.get(DB_ENV) or None
