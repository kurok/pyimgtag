"""Name auto-clustered people from labeled reference faces.

Apple Photos' People view holds the ground truth (name ↔ face), but on some
Photos.app builds it cannot be enumerated via AppleScript (the ``-2741``
"Expected class name" failure), so ``import-photos`` returns nothing. This
module offers an embedding-based escape hatch: given a folder of *labeled*
reference faces — one image (or sub-folder of images) per person — it matches
each auto-clustered person in the DB to the nearest reference and applies the
name, merging into an existing trusted person of that name when one exists.

The matching/applying logic here is pure and unit-tested with synthetic
embeddings; only :func:`load_reference_embeddings` touches the optional
``[face]`` dependency (it reuses the same detector/encoder as ``faces scan``).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

    from pyimgtag.progress_db import ProgressDB

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}

# Match thresholds mirror the Photos-import linker: a cluster is named only when
# its centroid is within _MATCH_THRESHOLD of a reference centroid AND clearly
# closer than the next-best name (by _MATCH_MARGIN). Conservative on purpose —
# a wrong name on a whole cluster is worse than leaving it "Person N".
_MATCH_THRESHOLD = 0.5
_MATCH_MARGIN = 0.05


@dataclass
class NameMatch:
    """A proposed naming of one auto cluster from a reference face."""

    person_id: int
    current_label: str
    name: str
    distance: float
    face_count: int


def _iter_reference_images(ref_dir: str | Path):
    """Yield ``(image_path, person_name)`` pairs from a reference folder.

    Two layouts are supported, mixable in one folder:
      - ``<dir>/<Name>.<ext>``         → one reference image, name = file stem
      - ``<dir>/<Name>/<anything>.<ext>`` → many images, name = sub-folder name
    """
    root = Path(ref_dir)
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            for img in sorted(entry.iterdir()):
                if img.is_file() and img.suffix.lower() in _IMAGE_EXTENSIONS:
                    yield img, entry.name
        elif entry.is_file() and entry.suffix.lower() in _IMAGE_EXTENSIONS:
            yield entry, entry.stem


def load_reference_embeddings(
    ref_dir: str | Path,
    *,
    max_dim: int = 1280,
    model: str = "hog",
    num_jitters: int = 1,
) -> dict[str, list[np.ndarray]]:
    """Detect the primary face in each reference image and embed it.

    Returns ``{person_name: [embedding, ...]}``. Images with no detectable
    face are skipped (logged). Requires the ``[face]`` extra at runtime.
    """
    from pyimgtag.face_detection import detect_faces
    from pyimgtag.face_embedding import compute_embeddings

    refs: dict[str, list[np.ndarray]] = defaultdict(list)
    for img_path, name in _iter_reference_images(ref_dir):
        try:
            faces = detect_faces(img_path, max_dim=max_dim, model=model)
        except Exception:  # noqa: BLE001 — one unreadable reference must not abort
            logger.warning("reference %s: could not read/detect — skipping", img_path)
            continue
        if not faces:
            logger.warning("reference %s: no face detected — skipping", img_path)
            continue
        # The largest detection is the portrait subject; ignore bystanders.
        faces.sort(key=lambda f: f.bbox_w * f.bbox_h, reverse=True)
        embeddings = compute_embeddings(
            img_path, faces[:1], max_dim=max_dim, num_jitters=num_jitters
        )
        if embeddings:
            refs[name].append(embeddings[0])
    return dict(refs)


def match_clusters_to_references(
    db: ProgressDB,
    references: dict[str, list[np.ndarray]],
    *,
    threshold: float = _MATCH_THRESHOLD,
    margin: float = _MATCH_MARGIN,
) -> list[NameMatch]:
    """Match each auto-clustered person to the nearest reference name.

    Only auto clusters (``trusted=0 AND confirmed=0``) with embedded faces are
    considered — trusted/confirmed people are already named and left alone. A
    cluster is matched only when its centroid is within ``threshold`` of a
    reference centroid and clearly closer than the runner-up (by ``margin``).
    """
    import numpy as np

    ref_centroids = {
        name: np.mean(np.stack(embs), axis=0) for name, embs in references.items() if embs
    }
    if not ref_centroids:
        return []

    auto_ids = db.get_auto_person_ids()
    matches: list[NameMatch] = []
    for person in db.get_persons():
        if person.person_id not in auto_ids:
            continue
        embeddings = db.get_person_embeddings(person.person_id)
        if not embeddings:
            continue
        centroid = np.mean(np.stack(embeddings), axis=0)
        ranked = sorted(
            ((name, float(np.linalg.norm(centroid - c))) for name, c in ref_centroids.items()),
            key=lambda t: t[1],
        )
        best_name, best_dist = ranked[0]
        next_dist = ranked[1][1] if len(ranked) > 1 else float("inf")
        if best_dist <= threshold and (next_dist - best_dist) >= margin:
            matches.append(
                NameMatch(
                    person_id=person.person_id,
                    current_label=person.label,
                    name=best_name,
                    distance=best_dist,
                    face_count=len(person.face_ids),
                )
            )
    return matches


def apply_matches(db: ProgressDB, matches: list[NameMatch]) -> dict[str, int]:
    """Apply proposed matches: merge into an existing trusted person of that
    name when one exists, otherwise rename the cluster (marking it trusted).

    Returns ``{"renamed": n, "merged": n}``.
    """
    trusted_by_name = {p.label: p.person_id for p in db.get_persons() if p.trusted and p.label}
    renamed = merged = 0
    for m in matches:
        target = trusted_by_name.get(m.name)
        if target is not None and target != m.person_id:
            # Fold the freshly-named cluster into the pre-existing named person.
            db.merge_persons(source_id=m.person_id, target_id=target)
            merged += 1
        else:
            db.update_person_label(m.person_id, m.name)
            renamed += 1
    return {"renamed": renamed, "merged": merged}
