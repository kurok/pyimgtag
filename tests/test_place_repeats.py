"""A city named like its region or country is one place node, not two (#476)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from pyimgtag.commands.export import build_tag_tree, tag_tree_to_xml
from pyimgtag.hierarchy import keyword_paths
from pyimgtag.models import ImageResult
from pyimgtag.place_names import distinct_place_parts


class TestDistinctPlaceParts:
    def test_drops_a_name_equal_to_its_neighbour(self):
        assert distinct_place_parts("Paris", "Paris", "Île-de-France", "France") == [
            "Paris",
            "Île-de-France",
            "France",
        ]

    def test_ignores_case_and_spaces(self):
        assert distinct_place_parts("New York", " new york ", "United States") == [
            "New York",
            "United States",
        ]

    def test_an_empty_middle_does_not_keep_equal_names_apart(self):
        assert distinct_place_parts("Singapore", None, "", "Singapore") == ["Singapore"]

    def test_only_neighbours_collapse(self):
        assert distinct_place_parts("A", "B", "A") == ["A", "B", "A"]

    def test_nothing_in_nothing_out(self):
        assert distinct_place_parts(None, " ") == []


class TestKeywordPaths:
    @pytest.mark.parametrize(
        ("country", "region", "city", "expected"),
        [
            ("Schweiz", "Zürich", "Zürich", "Places|Schweiz|Zürich"),
            ("United States", "New York", "New York", "Places|United States|New York"),
            ("Singapore", None, "Singapore", "Places|Singapore"),
            ("Deutschland", "Berlin", "Berlin", "Places|Deutschland|Berlin"),
            ("Deutschland", "Bayern", "München", "Places|Deutschland|Bayern|München"),
        ],
    )
    def test_place_path(self, country, region, city, expected):
        assert keyword_paths(country=country, region=region, city=city) == [expected]


def _no_node_repeats_its_parent(element: ET.Element) -> bool:
    for child in element:
        if child.get("name") == element.get("name"):
            return False
        if not _no_node_repeats_its_parent(child):
            return False
    return True


def test_digikam_export_has_no_node_inside_itself():
    rows = [
        {"tags": [], "nearest_country": "Schweiz", "nearest_region": "Zürich",
         "nearest_city": "Zürich"},
        {"tags": [], "nearest_country": "United States", "nearest_region": "New York",
         "nearest_city": "New York"},
    ]
    tree = build_tag_tree(rows)

    assert tree["Places"] == {"Schweiz": {"Zürich": {}}, "United States": {"New York": {}}}
    assert _no_node_repeats_its_parent(ET.fromstring(tag_tree_to_xml(tree)))


def test_description_does_not_repeat_the_city():
    result = ImageResult(
        file_path="/x.jpg",
        file_name="x.jpg",
        scene_summary="A lake",
        nearest_city="Zürich",
        nearest_region="Zürich",
        nearest_country="Schweiz",
    )
    description = result.build_description()
    assert description is not None
    assert "Zürich, Schweiz." in description
    assert "Zürich, Zürich" not in description


def test_run_location_line_does_not_repeat(capsys):
    from pyimgtag.commands.run import _print_verbose

    result = ImageResult(
        file_path="/x.jpg",
        file_name="x.jpg",
        nearest_place="Paris",
        nearest_city="Paris",
        nearest_region="Île-de-France",
        nearest_country="France",
    )
    _print_verbose(result, 1, 1, [])
    assert "Location: Paris, Île-de-France, France" in capsys.readouterr().err


def test_model_prompt_location_does_not_repeat():
    from pyimgtag.ollama_client import _build_prompt_with_context

    prompt = _build_prompt_with_context({"city": "Zürich", "region": "Zürich", "country": "Schweiz"})
    assert "- Location: Zürich, Schweiz" in prompt
