"""Spatiotemporal grouping of photos into events and trips.

Pure functions over plain records, like :mod:`pyimgtag.dedup_groups`: no
SQLite, no filesystem, no model call. That is what makes the boundary rules
testable against fabricated timelines rather than against a real library.

**Events** are what a person would call one occasion — an afternoon at the
beach, a birthday dinner. Photos are sorted by capture time and split wherever
either the clock or the map says the occasion ended: a gap longer than
``gap_hours``, or a jump further than ``distance_km``.

**Trips** are the tier above: consecutive events at the same place spanning
more than one day. A week in Lisbon is one trip containing seven days of
events, which is how people talk about it and how an album should be shaped.

Photos with no coordinates cluster on time alone -- half a library predates
GPS-tagged phones, and refusing to group those would make the feature useless
on exactly the archives people most want organised. Photos with no capture
date are not guessed at; they are returned separately for
``events list --unassigned``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

#: A gap longer than this ends an event. Six hours splits "morning at the
#: museum" from "dinner that evening" without splitting a long lunch.
DEFAULT_GAP_HOURS = 6.0

#: A jump further than this ends an event even if the clock says otherwise:
#: two cities in one afternoon is two occasions, not one.
DEFAULT_DISTANCE_KM = 50.0

#: Consecutive events closer than this, spanning more than one day, are one
#: trip. Wider than the event radius on purpose -- a trip is a region, and
#: day-trips out of a holiday base belong to the holiday.
DEFAULT_TRIP_RADIUS_KM = 150.0

#: Earth's mean radius, for the haversine below.
_EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class PhotoPoint:
    """One photo's position in time and space, as clustering sees it."""

    file_path: str
    taken_at: datetime | None
    lat: float | None = None
    lon: float | None = None
    #: Free-text hints the tagger already extracted, used for naming later.
    place: str | None = None
    event_hint: str | None = None
    tags: tuple[str, ...] = ()

    @property
    def has_location(self) -> bool:
        """True when both coordinates are present."""
        return self.lat is not None and self.lon is not None


@dataclass
class Event:
    """A contiguous run of photos that belong to one occasion."""

    members: list[PhotoPoint] = field(default_factory=list)
    #: Index of the trip this event belongs to, or None when it stands alone.
    trip: int | None = None

    @property
    def started_at(self) -> datetime:
        """Capture time of the first photo."""
        return self.members[0].taken_at  # type: ignore[return-value]

    @property
    def ended_at(self) -> datetime:
        """Capture time of the last photo."""
        return self.members[-1].taken_at  # type: ignore[return-value]

    @property
    def centroid(self) -> tuple[float, float] | None:
        """Mean coordinate of the located members, or None when none are."""
        located = [p for p in self.members if p.has_location]
        if not located:
            return None
        return (
            sum(p.lat for p in located) / len(located),  # type: ignore[misc]
            sum(p.lon for p in located) / len(located),  # type: ignore[misc]
        )

    @property
    def places(self) -> list[str]:
        """Distinct place names among the members, most common first."""
        counts: dict[str, int] = {}
        for point in self.members:
            if point.place:
                counts[point.place] = counts.get(point.place, 0) + 1
        return [name for name, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    @property
    def spans_days(self) -> int:
        """Number of distinct calendar days the event touches."""
        return len({p.taken_at.date() for p in self.members})  # type: ignore[union-attr]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres.

    Used rather than a flat approximation because a library can legitimately
    span the poles or the antimeridian, where a naive degree delta is wrong by
    any amount you like.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def _breaks_event(
    previous: PhotoPoint, current: PhotoPoint, gap_hours: float, distance_km: float
) -> bool:
    """True when *current* starts a new event rather than continuing one."""
    assert previous.taken_at is not None and current.taken_at is not None  # nosec B101
    if current.taken_at - previous.taken_at > timedelta(hours=gap_hours):
        return True
    # Only a pair that both have coordinates can be said to be far apart. One
    # missing reading is not evidence of travel.
    if previous.has_location and current.has_location:
        moved = haversine_km(
            previous.lat,  # type: ignore[arg-type]
            previous.lon,  # type: ignore[arg-type]
            current.lat,  # type: ignore[arg-type]
            current.lon,  # type: ignore[arg-type]
        )
        if moved > distance_km:
            return True
    return False


def detect_events(
    points: Iterable[PhotoPoint],
    gap_hours: float = DEFAULT_GAP_HOURS,
    distance_km: float = DEFAULT_DISTANCE_KM,
    trip_radius_km: float = DEFAULT_TRIP_RADIUS_KM,
) -> tuple[list[Event], list[PhotoPoint]]:
    """Group *points* into events, then link multi-day runs into trips.

    Returns ``(events, unassigned)``. *unassigned* holds the photos with no
    capture date: there is nothing to sort them by, and inventing a position
    for them would put arbitrary photos into somebody's album.

    Ordering is fully determined -- capture time, then path -- so two runs over
    the same library produce the same events in the same order.
    """
    dated: list[PhotoPoint] = []
    unassigned: list[PhotoPoint] = []
    for point in points:
        (dated if point.taken_at is not None else unassigned).append(point)

    # file_path breaks ties so a burst captured within one second does not
    # reorder between runs.
    dated.sort(key=lambda p: (p.taken_at, p.file_path))  # type: ignore[arg-type,return-value]
    unassigned.sort(key=lambda p: p.file_path)

    events: list[Event] = []
    for point in dated:
        if events and not _breaks_event(events[-1].members[-1], point, gap_hours, distance_km):
            events[-1].members.append(point)
        else:
            events.append(Event(members=[point]))

    _link_trips(events, trip_radius_km)
    return events, unassigned


def _link_trips(events: Sequence[Event], trip_radius_km: float) -> None:
    """Tag consecutive same-place events that span more than a day as one trip.

    Mutates ``Event.trip`` in place. A run is only a trip once it covers two or
    more calendar days -- otherwise every busy Saturday would become one.
    """
    trip_index = 0
    run_start = 0
    for i in range(1, len(events) + 1):
        continues = False
        if i < len(events):
            previous, current = events[i - 1], events[i]
            here, there = previous.centroid, current.centroid
            # Without coordinates the only evidence is the calendar: adjacent
            # days stay together, a longer gap does not.
            if here is None or there is None:
                continues = (current.started_at.date() - previous.ended_at.date()).days <= 1
            else:
                continues = (
                    haversine_km(here[0], here[1], there[0], there[1]) <= trip_radius_km
                    and (current.started_at.date() - previous.ended_at.date()).days <= 1
                )
        if not continues:
            run = events[run_start:i]
            days = {p.taken_at.date() for e in run for p in e.members}  # type: ignore[union-attr]
            if len(run) > 1 and len(days) > 1:
                for event in run:
                    event.trip = trip_index
                trip_index += 1
            run_start = i


def fallback_name(event: Event) -> str:
    """A name for an event without asking a model anything.

    ``"Lisbon — June 2024"`` when a place is known, the date range otherwise.
    Deterministic, offline, and good enough that naming is optional rather
    than a prerequisite for using the feature at all.
    """
    when = event.started_at
    span = f"{when:%B %Y}"
    if event.started_at.date() != event.ended_at.date() and (
        event.started_at.month != event.ended_at.month
        or event.started_at.year != event.ended_at.year
    ):
        span = f"{when:%B %Y} – {event.ended_at:%B %Y}"

    places = event.places
    if places:
        return f"{places[0]} — {span}"
    if event.spans_days > 1:
        return f"{event.started_at:%-d %b} – {event.ended_at:%-d %b %Y}"
    return f"{event.started_at:%-d %B %Y}"
