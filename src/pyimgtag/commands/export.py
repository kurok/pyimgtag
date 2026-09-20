"""Handler for the ``export`` subcommand: get the whole library out.

Three formats, one job: hand the enrichment to whatever the user keeps their
photos in. ``csv``/``json`` are the flat dumps; ``digikam`` is a tag-tree XML
for digiKam's tag importer.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# nosec B405 — B405 is about *parsing* untrusted XML. This module only builds
# it: Element/SubElement/indent/tostring, no parse() and no input document
# anywhere. defusedxml hardens the parser, which is not the surface in use.
from xml.etree import ElementTree as ET  # nosec B405

from pyimgtag.progress_db import ProgressDB


def build_tag_tree(rows: list[dict], people: dict[str, set[str]] | None = None) -> dict:
    """Fold export rows into a nested ``{name: {child: {...}}}`` tag tree.

    The taxonomy itself lives in :mod:`pyimgtag.hierarchy`, shared with the
    hierarchical XMP keywords the tagger writes. That is deliberate: a library
    exported here and the same library tagged with ``--hierarchical-keywords``
    have to land in the same branches, or importing both produces two parallel
    trees saying the same thing.

    *people* maps a person's name to the paths they appear in, so a named face
    becomes ``People|Alice``. Omitted when the face pipeline has not run.
    """
    from pyimgtag.hierarchy import keyword_paths, tree_from_paths

    paths: set[str] = set()
    for row in rows:
        paths.update(
            keyword_paths(
                row.get("tags"),
                country=row.get("nearest_country"),
                region=row.get("nearest_region"),
                city=row.get("nearest_city"),
                event=row.get("event_name"),
            )
        )
    paths.update(keyword_paths(people=sorted(people or {})))
    return tree_from_paths(paths)


def tag_tree_to_xml(tree: dict) -> str:
    """Render a tag tree as digiKam's ``<digikam-tags>`` XML.

    Children are emitted in sorted order so two exports of the same library
    produce byte-identical files and a diff means something changed.
    """
    root = ET.Element("digikam-tags")

    def _add(parent: ET.Element, node: dict) -> None:
        for name in sorted(node):
            element = ET.SubElement(parent, "tag", {"name": name})
            _add(element, node[name])

    _add(root, tree)
    ET.indent(root, space="  ")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE digikam-tags>\n" + ET.tostring(root, encoding="unicode") + "\n"
    )


def _people_map(db: ProgressDB) -> dict[str, set[str]]:
    """Named people and the photos they appear in, or empty without faces."""
    try:
        return {
            person.label: db.paths_for_person_label(person.label)
            for person in db.get_persons()
            if person.label
        }
    except Exception:  # noqa: BLE001 — a library with no face tables still exports
        return {}


def cmd_export(args: argparse.Namespace) -> int:
    """Execute the export subcommand."""
    from pyimgtag.db.image_db import ImageDB
    from pyimgtag.output_writer import write_rows_csv, write_rows_json

    output = Path(args.output).expanduser()
    with ProgressDB(db_path=args.db) as db:
        rows = db.export_rows()
        if not rows:
            print("Nothing to export: the database has no processed images.", file=sys.stderr)
            return 0
        people = _people_map(db) if args.format == "digikam" else {}

    try:
        if args.format == "csv":
            write_rows_csv(rows, output, ImageDB.EXPORT_COLUMNS)
        elif args.format == "json":
            write_rows_json(rows, output)
        else:
            output.write_text(tag_tree_to_xml(build_tag_tree(rows, people)), encoding="utf-8")
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Exported {len(rows)} row(s) to {output}", file=sys.stderr)
    return 0
