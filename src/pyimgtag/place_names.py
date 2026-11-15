"""One name per place field: the rule shared by the geocoder and the DB.

Kept free of third-party imports, so the progress DB (imported by the CLI at
start) can use it without pulling in ``requests``.
"""

from __future__ import annotations

__all__ = ["COUNTRY_SEPARATORS", "LOCALITY_SEPARATORS", "REGION_SEPARATORS", "native_name"]

# Multilingual places carry every official name in their OSM ``name`` tag,
# joined by a separator that depends on the mappers' convention (sampled from
# real Nominatim replies):
#   country   "Schweiz / Suisse / Svizzera / Svizra", "België / Belgique / Belgien"
#   region    "Valais/Wallis", "Bern/Berne",
#             "Région de Bruxelles-Capitale - Brussels Hoofdstedelijk Gewest"
# By convention the first name is the majority/local language, so that one
# is kept.
COUNTRY_SEPARATORS = (" / ", "/")
REGION_SEPARATORS = (" / ", "/", " - ")
# Cities and places are left alone apart from the spaced slash: a bare slash or
# a spaced hyphen there ("Biel/Bienne", "Bolzano - Bozen") is usually the
# locally used double name, not a list to pick from.
LOCALITY_SEPARATORS = (" / ",)


def native_name(name: str | None, separators: tuple[str, ...] = (" / ",)) -> str | None:
    """Return the first name of a multilingual place name.

    ``"Schweiz / Suisse / Svizzera / Svizra"`` becomes ``"Schweiz"``. Only the
    given *separators* split; a name without one is returned unchanged.
    """
    if not name:
        return name
    for separator in separators:
        if separator in name:
            first = name.split(separator, 1)[0].strip()
            return first or name
    return name
