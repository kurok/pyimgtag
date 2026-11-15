"""One name per place field: the rule shared by the geocoder and the DB.

Kept free of third-party imports, so the progress DB (imported by the CLI at
start) can use it without pulling in ``requests``.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable

__all__ = [
    "COUNTRY_SEPARATORS",
    "LOCALITY_SEPARATORS",
    "REGION_SEPARATORS",
    "fold",
    "native_name",
    "place_like_patterns",
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
    compares equal. Registered as an SQL function by the progress DB, since
    SQLite's own ``LOWER`` folds ASCII only.
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


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def place_like_patterns(typed: str, kind: str) -> list[str]:
    """Folded SQL ``LIKE`` patterns (escape ``\\``) a place filter should match.

    ``"Switzerland"`` for a country gives ``["%switzerland%", "%schweiz%"]``,
    so the stored native name is found. A two-letter country code, or a short
    alias such as ``"UK"``, is matched whole rather than as a substring
    (``"US"`` must not match "Australia" or "Belarus"); it finds the native
    name and a row that stores the code itself. Anything not in the tables is
    matched as typed.

    Args:
        typed: The filter value as the user gave it.
        kind: ``"country"`` or ``"city"``.

    Returns:
        De-duplicated patterns, matched against the folded stored name; a row
        matches when any of them does.
    """
    key = fold(typed.strip()) or ""
    index = _COUNTRY_INDEX if kind == "country" else _CITY_INDEX
    natives = list(index.get(key, ()))
    if kind == "country" and key in _COUNTRY_CODES:
        natives = [_COUNTRY_CODES[key]]
    short = bool(natives) and len(key) <= 3
    patterns = [_like_escape(key) if short else f"%{_like_escape(key)}%"]
    for native in natives:
        pattern = f"%{_like_escape(fold(native) or '')}%"
        if pattern not in patterns:
            patterns.append(pattern)
    return patterns
