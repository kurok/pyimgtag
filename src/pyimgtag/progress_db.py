"""Compatibility re-export for the progress database (moved to :mod:`pyimgtag.db`).

The SQLite progress database was decomposed into the ``pyimgtag.db``
package (issue #282): :class:`~pyimgtag.db.progress_db.ProgressDB` is now a
thin facade over the ``ImageDB`` / ``FaceDB`` / ``JudgeDB`` domain helpers.
This module keeps the historical ``pyimgtag.progress_db`` import path
working unchanged for all existing callers.
"""

from __future__ import annotations

from pyimgtag.db.image_db import _DEFAULT_PATH_BATCH_SIZE
from pyimgtag.db.judge_db import _DEFAULT_JUDGE_RESULTS_LIMIT
from pyimgtag.db.progress_db import ProgressDB
from pyimgtag.models import FaceDetection, ImageResult, PersonCluster

__all__ = [
    "_DEFAULT_JUDGE_RESULTS_LIMIT",
    "_DEFAULT_PATH_BATCH_SIZE",
    "FaceDetection",
    "ImageResult",
    "PersonCluster",
    "ProgressDB",
]
