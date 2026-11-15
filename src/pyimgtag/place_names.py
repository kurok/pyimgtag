"""One name per place field: the rule shared by the geocoder and the DB.

Kept free of third-party imports, so the progress DB (imported by the CLI at
start) can use it without pulling in ``requests``.
"""

from __future__ import annotations

import functools
import unicodedata
from collections.abc import Iterable

__all__ = [
    "COUNTRY_SEPARATORS",
    "LOCALITY_SEPARATORS",
    "REGION_SEPARATORS",
    "distinct_place_parts",
    "fold",
    "native_name",
    "place_matches",
    "place_terms",
]

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


def distinct_place_parts(*parts: str | None) -> list[str]:
    """The non-empty place names in order, without repeating a neighbour.

    A city often shares its name with its region or country (Zürich in canton
    Zürich, New York in New York state, Singapore), and Nominatim's place and
    city are often the same too. Listing both reads as a folder inside itself
    (``Places|Schweiz|Zürich|Zürich``, "Paris, Paris, Île-de-France"), so a
    name equal to the one before it (ignoring case and surrounding spaces) is
    dropped. Empty parts are skipped first, so a missing region does not keep
    an equal country and city apart.

    Args:
        *parts: Place names from the widest or the narrowest end, in order.

    Returns:
        The cleaned names, each different from its predecessor.
    """
    kept: list[str] = []
    for part in parts:
        name = str(part).strip() if part is not None else ""
        if name and (not kept or kept[-1].casefold() != name.casefold()):
            kept.append(name)
    return kept


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


# --- Matching a typed place name against the stored native one -------------
#
# The DB keeps one native name per place ("Schweiz", "München"), so a filter
# typed in English ("Switzerland", "Munich") or as a country code ("CH")
# would find nothing. The tables below map those spellings to the stored
# native name. They are a curated subset (the countries and cities a photo
# library most often holds), not an atlas; a miss is answered by the query
# command listing the names the library actually has.

#: (ISO 3166-1 alpha-2 code, stored native name, other names people type).
_COUNTRIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("al", "Shqipëria", ("Albania",)),
    ("ad", "Andorra", ()),
    ("ar", "Argentina", ()),
    ("am", "Հայաստան", ("Armenia",)),
    ("at", "Österreich", ("Austria", "Autriche")),
    ("au", "Australia", ()),
    ("ba", "Bosna i Hercegovina", ("Bosnia and Herzegovina", "Bosnia")),
    ("be", "België", ("Belgium", "Belgique", "Belgien")),
    ("bg", "България", ("Bulgaria",)),
    ("br", "Brasil", ("Brazil",)),
    ("by", "Беларусь", ("Belarus",)),
    ("ca", "Canada", ()),
    ("ch", "Schweiz", ("Switzerland", "Suisse", "Svizzera", "Svizra")),
    ("cl", "Chile", ()),
    ("cn", "中国", ("China",)),
    ("co", "Colombia", ()),
    ("cy", "Κύπρος", ("Cyprus",)),
    ("cz", "Česko", ("Czechia", "Czech Republic")),
    ("de", "Deutschland", ("Germany", "Allemagne")),
    ("dk", "Danmark", ("Denmark",)),
    ("ee", "Eesti", ("Estonia",)),
    ("eg", "مصر", ("Egypt",)),
    ("es", "España", ("Spain", "Espagne")),
    ("fi", "Suomi", ("Finland",)),
    ("fr", "France", ("Frankreich", "Francia")),
    ("gb", "United Kingdom", ("UK", "Great Britain", "Britain")),
    ("ge", "საქართველო", ("Georgia",)),
    ("gr", "Ελλάς", ("Greece", "Hellas", "Ελλάδα")),
    ("hr", "Hrvatska", ("Croatia",)),
    ("hu", "Magyarország", ("Hungary",)),
    ("id", "Indonesia", ()),
    ("ie", "Éire", ("Ireland",)),
    ("il", "ישראל", ("Israel",)),
    ("in", "India", ()),
    ("is", "Ísland", ("Iceland",)),
    ("it", "Italia", ("Italy", "Italie", "Italien")),
    ("jp", "日本", ("Japan",)),
    ("kr", "대한민국", ("South Korea", "Korea")),
    ("li", "Liechtenstein", ()),
    ("lt", "Lietuva", ("Lithuania",)),
    ("lu", "Lëtzebuerg", ("Luxembourg", "Luxemburg")),
    ("lv", "Latvija", ("Latvia",)),
    ("mc", "Monaco", ()),
    ("me", "Crna Gora", ("Montenegro",)),
    ("mk", "Северна Македонија", ("North Macedonia", "Macedonia")),
    ("mt", "Malta", ()),
    ("mx", "México", ("Mexico",)),
    ("my", "Malaysia", ()),
    ("nl", "Nederland", ("Netherlands", "Holland", "The Netherlands")),
    ("no", "Norge", ("Norway",)),
    ("nz", "New Zealand", ("Aotearoa",)),
    ("pe", "Perú", ("Peru",)),
    ("ph", "Pilipinas", ("Philippines",)),
    ("pl", "Polska", ("Poland",)),
    ("pt", "Portugal", ()),
    ("ro", "România", ("Romania",)),
    ("rs", "Србија", ("Serbia",)),
    ("ru", "Россия", ("Russia",)),
    ("se", "Sverige", ("Sweden",)),
    ("si", "Slovenija", ("Slovenia",)),
    ("sk", "Slovensko", ("Slovakia",)),
    ("sm", "San Marino", ()),
    ("th", "ประเทศไทย", ("Thailand",)),
    ("tr", "Türkiye", ("Turkey",)),
    ("ua", "Україна", ("Ukraine",)),
    ("us", "United States", ("USA", "United States of America", "America")),
    ("vn", "Việt Nam", ("Vietnam",)),
    ("za", "South Africa", ()),
)

#: Stored native city name -> exonyms people type. Only names that differ by
#: more than accents: "Zurich" already finds "Zürich" through :func:`fold`.
_CITIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Antwerpen", ("Antwerp", "Anvers")),
    ("Αθήνα", ("Athens",)),
    ("Београд", ("Belgrade",)),
    ("București", ("Bucharest",)),
    ("Bruxelles", ("Brussels", "Brussel")),
    ("Brugge", ("Bruges",)),
    ("Ciudad de México", ("Mexico City",)),
    ("Den Haag", ("The Hague",)),
    ("Firenze", ("Florence",)),
    ("Genève", ("Geneva", "Genf")),
    ("Genova", ("Genoa",)),
    ("Gent", ("Ghent",)),
    ("Göteborg", ("Gothenburg",)),
    ("Hannover", ("Hanover",)),
    ("Köln", ("Cologne",)),
    ("København", ("Copenhagen",)),
    ("Київ", ("Kyiv", "Kiev")),
    ("Lisboa", ("Lisbon",)),
    ("Luzern", ("Lucerne",)),
    ("Marseille", ("Marseilles",)),
    ("Milano", ("Milan",)),
    ("Москва", ("Moscow",)),
    ("München", ("Munich",)),
    ("Napoli", ("Naples",)),
    ("Nürnberg", ("Nuremberg",)),
    ("Padova", ("Padua",)),
    ("Praha", ("Prague",)),
    ("Roma", ("Rome",)),
    ("Санкт-Петербург", ("Saint Petersburg", "St Petersburg")),
    ("Sevilla", ("Seville",)),
    ("София", ("Sofia",)),
    ("Torino", ("Turin",)),
    ("Venezia", ("Venice",)),
    ("Warszawa", ("Warsaw",)),
    ("Wien", ("Vienna",)),
)


def fold(text: str | None) -> str | None:
    """Fold *text* for matching: no case, no accents ("Zürich" -> "zurich").

    Decomposes to NFKD, drops the combining marks and casefolds, so a name
    stored in either Unicode form and typed with or without its accents
    compares equal (SQLite's own ``LOWER`` folds ASCII only).
    """
    if text is None:
        return None
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _alias_index(entries: Iterable[tuple[str, Iterable[str]]]) -> dict[str, tuple[str, ...]]:
    index: dict[str, list[str]] = {}
    for native, names in entries:
        for name in (native, *names):
            key = fold(name) or ""
            if native not in index.setdefault(key, []):
                index[key].append(native)
    return {k: tuple(v) for k, v in index.items()}


_COUNTRY_INDEX = _alias_index((native, names) for _, native, names in _COUNTRIES)
_COUNTRY_CODES = {code: native for code, native, _ in _COUNTRIES}
_CITY_INDEX = _alias_index(_CITIES)


@functools.lru_cache(maxsize=256)
def place_terms(typed: str, kind: str) -> tuple[frozenset[str], tuple[str, ...]]:
    """Folded terms a place filter matches for what the user *typed*.

    ``"Switzerland"`` for a country gives substrings ``("switzerland",
    "schweiz")``, so the stored native name is found. A two-letter country
    code, known or not, and a short alias such as ``"UK"`` are matched whole
    rather than as substrings (``"US"`` must not match "Australia", ``"NA"``
    must not match "Canada"); a known one also finds its native name, and a
    row that stores the code itself still matches. Anything else is matched
    as typed.

    Args:
        typed: The filter value as the user gave it.
        kind: ``"country"`` or ``"city"``.

    Returns:
        ``(exact, substrings)``: a stored name matches when, folded, it equals
        one of *exact* or contains one of *substrings*.
    """
    key = fold(typed.strip()) or ""
    index = _COUNTRY_INDEX if kind == "country" else _CITY_INDEX
    natives = list(index.get(key, ()))
    is_code = kind == "country" and len(key) == 2 and key.isascii() and key.isalpha()
    if is_code and key in _COUNTRY_CODES:
        natives = [_COUNTRY_CODES[key]]
    whole = is_code or (bool(natives) and len(key) <= 3)
    substrings = [] if whole else [key]
    for native in natives:
        folded = fold(native) or ""
        if folded not in substrings:
            substrings.append(folded)
    return (frozenset({key}) if whole else frozenset()), tuple(substrings)


def place_matches(stored: str | None, typed: str, kind: str) -> bool:
    """True when the *stored* place name answers a filter for *typed*.

    Registered as an SQL function by the progress DB, so each row's name is
    folded once however many spellings the filter accepts.

    Args:
        stored: The place name in the DB (may be None).
        typed: The filter value as the user gave it.
        kind: ``"country"`` or ``"city"``.

    Returns:
        Whether the row matches; a row with no name never does.
    """
    if not stored:
        return False
    exact, substrings = place_terms(typed, kind)
    folded = fold(stored) or ""
    return folded in exact or any(term in folded for term in substrings)
