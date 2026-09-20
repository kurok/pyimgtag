"""Handler for the ``export`` subcommand: get the whole library out.

Three formats, one job: hand the enrichment to whatever the user keeps their
photos in. ``csv``/``json`` are the flat dumps; ``digikam`` is a tag-tree XML
for digiKam's tag importer.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

from pyimgtag.progress_db import ProgressDB

#: Top-level branches of the exported tag tree. The same shape the
#: hierarchical-keyword writing in #357 targets, so a library exported here and
#: a library tagged there land in the same places in digiKam.
_PEOPLE = "People"
_PLACES = "Places"
_TAGS = "Tags"
_EVENTS = "Events"


def build_tag_tree(rows: list[dict], people: dict[str, set[str]] | None = None) -> dict:
    """Fold export rows into a nested ``{name: {child: {...}}}`` tag tree.

    ``Places`` is built from the geocoded fields in country → region → city
    order, which is how every photo manager expects a place hierarchy and the
    only order in which the branches merge usefully: a hundred photos from one
    country share one node rather than a hundred sibling city nodes.

    *people* maps a person's name to the paths they appear in, so a named face
    becomes ``People|Alice``. Omitted when the face pipeline has not run.
    """
    tree: dict = {}

    def _branch(*path: str) -> None:
        """Insert a path, skipping empty segments so gaps do not create blanks."""
        node = tree
        for part in path:
            if not part:
                # A photo geocoded to a country but no city must not produce a
                # nameless child between them.
                continue
            node = node.setdefault(str(part).strip(), {})

    for row in rows:
        for tag in row.get("tags") or []:
            _branch(_TAGS, tag)
        if row.get("nearest_country") or row.get("nearest_city"):
            _branch(
                _PLACES,
                row.get("nearest_country") or "",
                row.get("nearest_region") or "",
                row.get("nearest_city") or "",
            )
        if row.get("event_name"):
            _branch(_EVENTS, row["event_name"])

    for name in sorted(people or {}):
        _branch(_PEOPLE, name)
    return tree


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
