"""Snapshot test for the public API surface of :class:`ProgressDB`.

The baseline below was captured from ``pyimgtag.progress_db.ProgressDB``
BEFORE the god-class decomposition into the ``pyimgtag.db`` package
(issue #282). The facade must keep exactly this public surface so that
every existing caller keeps working unchanged.
"""

from __future__ import annotations

from pyimgtag.progress_db import ProgressDB

# Sorted public attribute names of ProgressDB captured pre-refactor.
# If a method is intentionally added or removed, update this list in the
# same PR and call the API change out explicitly in the description.
EXPECTED_PUBLIC_API = [
    "clear_auto_persons",
    "close",
    "confirm_person",
    "confirm_persons",
    "count_auto_persons",
    "count_images",
    "create_person",
    "delete_image",
    "delete_image_rows",
    "delete_person",
    "delete_persons",
    "delete_tag",
    "get_all_embeddings",
    "get_all_judge_results",
    "get_assigned_faces",
    "get_auto_person_ids",
    "get_cached_result",
    "get_cleanup_candidates",
    "get_clusterable_embeddings",
    "get_embeddings_for_faces",
    "get_face_by_id",
    "get_face_count",
    "get_faces_by_uuid",
    "get_faces_for_image",
    "get_faces_for_person",
    "get_ignored_faces",
    "get_image",
    "get_images",
    "get_judge_result",
    "get_known_file_path",
    "get_person_embeddings",
    "get_persons",
    "get_photos_person_id",
    "get_stats",
    "get_tag_counts",
    "get_unassigned_faces",
    "has_photos_person",
    "has_usable_model_result",
    "ignore_face",
    "insert_face",
    "is_complete_cached",
    "is_face_scanned",
    "is_fresh",
    "is_processed",
    "iter_image_paths",
    "mark_done",
    "mark_face_scanned",
    "merge_persons",
    "merge_tags",
    "path",
    "query_images",
    "query_judge_results",
    "rename_tag",
    "reset_all",
    "reset_all_faces",
    "reset_by_status",
    "reset_untrusted_faces",
    "restore_face",
    "save_judge_result",
    "set_person_id",
    "unassign_face",
    "update_image_cleanup",
    "update_image_tags",
    "update_missing_fields",
    "update_person_label",
]


def test_progress_db_public_api_unchanged():
    """The sorted public method/attribute list must match the pre-refactor baseline."""
    actual = sorted(name for name in dir(ProgressDB) if not name.startswith("_"))
    assert actual == EXPECTED_PUBLIC_API
