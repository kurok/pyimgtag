"""SQLite persistence package: progress DB facade plus per-domain helpers.

``ProgressDB`` (the public entry point) owns the connection, the schema,
and all versioned migrations; ``ImageDB`` / ``FaceDB`` / ``JudgeDB`` hold
the per-domain query logic and share the facade's connection.
"""

from __future__ import annotations

from pyimgtag.db.face_db import FaceDB
from pyimgtag.db.image_db import _DEFAULT_PATH_BATCH_SIZE, ImageDB
from pyimgtag.db.judge_db import _DEFAULT_JUDGE_RESULTS_LIMIT, JudgeDB
from pyimgtag.db.progress_db import ProgressDB
from pyimgtag.models import FaceDetection, ImageResult, PersonCluster

__all__ = [
    "_DEFAULT_JUDGE_RESULTS_LIMIT",
    "_DEFAULT_PATH_BATCH_SIZE",
    "FaceDB",
    "FaceDetection",
    "ImageDB",
    "ImageResult",
    "JudgeDB",
    "PersonCluster",
    "ProgressDB",
]
