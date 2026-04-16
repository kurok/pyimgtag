"""Face clustering via DBSCAN over 128-d face embeddings."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB

logger = logging.getLogger(__name__)

_DEFAULT_EPS = 0.5
_DEFAULT_MIN_SAMPLES = 2


def cluster_faces(
    db: ProgressDB,
    eps: float = _DEFAULT_EPS,
    min_samples: int = _DEFAULT_MIN_SAMPLES,
) -> dict[int, list[int]]:
    """Cluster face embeddings with DBSCAN and persist person assignments.

    Creates a new person row for each cluster and sets ``person_id`` on
    every face that belongs to that cluster.  Faces labelled as noise
    (DBSCAN label -1) are left unassigned.

    Args:
        db: ProgressDB instance with populated face/embedding rows.
        eps: DBSCAN neighbourhood radius.  Smaller values produce tighter,
            more numerous clusters.  ``0.5`` is a reasonable starting point
            for 128-d face_recognition encodings (Euclidean distance).
        min_samples: Minimum faces required to form a cluster.

    Returns:
        Mapping of ``person_id -> [face_id, ...]`` for every cluster
        created.  Noise faces are **not** included.
    """
    import numpy as np

    try:
        from sklearn.cluster import DBSCAN
    except ImportError:
        raise ImportError(
            "scikit-learn is not installed. Install the [face] extra: pip install pyimgtag[face]"
        ) from None

    rows = db.get_all_embeddings()
    if not rows:
        return {}

    face_ids = [r[0] for r in rows]
    embeddings = np.array([r[1] for r in rows])

    labels = DBSCAN(eps=eps, min_samples=min_samples, metric="euclidean").fit_predict(embeddings)

    # Group face_ids by cluster label, skipping noise (-1)
    clusters: dict[int, list[int]] = {}
    for face_id, label in zip(face_ids, labels):
        if label == -1:
            continue
        clusters.setdefault(int(label), []).append(face_id)

    # Persist: create person rows and assign faces
    result: dict[int, list[int]] = {}
    for cluster_label in sorted(clusters):
        fids = clusters[cluster_label]
        person_id = db.create_person(label=f"Person {cluster_label + 1}")
        for fid in fids:
            db.set_person_id(fid, person_id)
        result[person_id] = fids

    return result
