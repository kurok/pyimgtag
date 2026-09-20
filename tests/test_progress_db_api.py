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
#
# Added by semantic search (#323): needs_embedding, upsert_embedding,
# clear_embeddings, embedding_stats and search_similar delegate to SearchDB,
# and paths_for_person_label delegates to FaceDB so `search --person` can
# resolve a name to the photos it appears in.
#
# Added by query-by-example (#324): get_embedding, which reads back a stored
# vector so `--similar-to` on an indexed photo needs no model.
#
# Added by events & trips (#325): list_events, get_event, event_paths,
# event_for_path, event_stats, rename_event, set_event_name, unnamed_events,
# reconcile_events and clear_events delegate to EventsDB. All additions;
# nothing has been removed or changed.
EXPECTED_PUBLIC_API = [
    "all_phashes",
    "clear_auto_persons",
    "clear_embeddings",
    "clear_events",
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
    "embedding_stats",
    "event_for_path",
    "event_paths",
    "event_stats",
    "export_rows",
    "get_all_embeddings",
    "get_all_judge_results",
    "get_assigned_faces",
    "get_auto_person_ids",
    "get_cached_result",
    "get_cleanup_candidates",
    "get_clusterable_embeddings",
    "get_dedup_group",
    "get_dedup_totals",
    "get_embedding",
    "get_embeddings_for_faces",
    "get_event",
    "get_face_by_id",
    "get_face_count",
    "get_faces_by_uuid",
    "get_faces_for_image",
    "get_faces_for_person",
    "get_ignored_faces",
    "get_image",
    "get_images",
    "get_insights",
    "get_judge_result",
    "get_known_file_path",
    "get_person_embeddings",
    "get_persons",
    "get_photos_person_id",
    "get_stats",
    "get_tag_counts",
    "get_unassigned_faces",
    "gps_coverage",
    "has_photos_person",
    "has_usable_model_result",
    "ignore_face",
    "insert_face",
    "is_complete_cached",
    "is_face_scanned",
    "is_fresh",
    "is_processed",
    "iter_image_paths",
    "iter_paths_missing_phash",
    "list_dedup_groups",
    "list_events",
    "map_clusters",
    "mark_dedup_resolved",
    "mark_done",
    "mark_face_scanned",
    "merge_persons",
    "merge_tags",
    "needs_embedding",
    "path",
    "paths_for_person_label",
    "query_images",
    "query_judge_results",
    "reconcile_events",
    "record_dedup_action",
    "rename_event",
    "rename_tag",
    "replace_unresolved_dedup_groups",
    "reset_all",
    "reset_all_faces",
    "reset_by_status",
    "reset_untrusted_faces",
    "restore_face",
    "save_judge_result",
    "search_similar",
    "set_event_name",
    "set_person_id",
    "set_phash",
    "timeline_days",
    "timeline_months",
    "unassign_face",
    "undo_dedup_group",
    "unnamed_events",
    "update_image_cleanup",
    "update_image_tags",
    "update_missing_fields",
    "update_person_label",
    "upsert_embedding",
]


def test_progress_db_public_api_unchanged():
    """The sorted public method/attribute list must match the pre-refactor baseline."""
    actual = sorted(name for name in dir(ProgressDB) if not name.startswith("_"))
    assert actual == EXPECTED_PUBLIC_API
