"""Carrier touchdown detection against a deck that floats 20 m above the sea.

This is the case the detector could not see at all. Measured on the
production database 2026-09-05:

- ``on_ground`` is absent from every one of 87,780,044 track rows, so the
  weight-on-wheels test always falls through to the AGL comparison;
- Tacview's ``AGL`` is height above the TERRAIN, which over water is the sea
  surface -- an aircraft parked on a Nimitz deck reports ~22 m;
- ``wow_agl_threshold_m`` is 3 m.

So an aircraft that traps never registered as on-deck, and the five
"carrier landings" the server had recorded were objects that hit the WATER
within 800 m of a ship: touchdown altitudes of -3.75, -2.96, -1.12, -1.03
and +2.78 m, two of them AIM-120 missiles.

The existing carrier fixtures set ``on_ground`` explicitly, which is why the
suite never saw this: they hand the detector the answer that production
never has. These build tracks the way the real data looks -- ``on_ground``
None, ``agl`` measured to the sea.
"""

from __future__ import annotations

import math

import pytest

from app.detection.detector import (
    CarrierState,
    DetectionConfig,
    TrackSample,
    analyze_track,
)

LAT0, LON0 = 35.0, 140.0
DECK_ALTITUDE_M = 22.0  # Nimitz, per config/carriers.yaml
M_PER_DEG_LAT = 111_320.0


def _lat_offset(meters: float) -> float:
    return meters / M_PER_DEG_LAT


def carrier(altitude_m: float = 0.0) -> dict[str, CarrierState]:
    """A stationary ship whose ACMI altitude is the waterline, as DCS reports."""
    return {
        "C1": CarrierState(
            obj_id="C1",
            name="CVN_73",
            type="Sea+Watercraft+AircraftCarrier",
            samples=[(t, LAT0, LON0, altitude_m, 0.0, 0.0) for t in (-120.0, 120.0)],
        )
    }


def approach(*, final_altitude_m: float, speed_ms: float = 65.0) -> list[TrackSample]:
    """A 3.5 deg approach to the ship, levelling at ``final_altitude_m``.

    ``agl`` is filled in the way Tacview does over water: height above the
    SEA, i.e. equal to the MSL altitude. ``on_ground`` is None throughout,
    exactly as every row in the production database has it.
    """
    tan_slope = math.tan(math.radians(3.5))
    samples: list[TrackSample] = []
    for t in range(-40, 21):
        if t < 0:
            distance = abs(t) * speed_ms
            altitude = final_altitude_m + distance * tan_slope
            latitude = LAT0 - _lat_offset(distance)
        else:
            altitude = final_altitude_m
            latitude = LAT0
        samples.append(
            TrackSample(
                time=float(t),
                latitude=latitude,
                longitude=LON0,
                altitude=altitude,
                agl=altitude,  # Tacview measures to the sea, not to the deck
                speed=speed_ms if t < 0 else max(5.0, speed_ms - t * 3.0),
                heading=0.0,
                on_ground=None,
            )
        )
    return samples


def deck_altitude_for(_carrier) -> float:
    return DECK_ALTITUDE_M


def test_a_trap_is_invisible_without_a_deck_reference() -> None:
    """The bug, pinned so it cannot come back.

    With no deck resolver the aircraft is judged against the sea, sits 22 m
    "up" for the whole recording, and no landing is reported.
    """
    events = analyze_track(
        approach(final_altitude_m=DECK_ALTITUDE_M),
        None,
        carrier(),
        config=DetectionConfig(),
    )
    assert events == []


def test_a_trap_on_the_deck_is_detected() -> None:
    events = analyze_track(
        approach(final_altitude_m=DECK_ALTITUDE_M),
        None,
        carrier(),
        config=DetectionConfig(),
        deck_altitude_for=deck_altitude_for,
    )
    assert len(events) == 1
    event = events[0]
    assert event.kind == "carrier"
    assert event.outcome == "full_stop"
    assert event.touchdown.time == pytest.approx(0.0, abs=1.5)
    # The reference recorded with the touchdown is the deck, so everything
    # downstream that asks "how high above the landing surface" gets the
    # answer this whole module exists to produce.
    assert event.touchdown.surface_is_deck is True
    assert event.touchdown.ground_altitude_m == pytest.approx(DECK_ALTITUDE_M)


def test_hitting_the_water_beside_the_ship_is_not_a_trap() -> None:
    """The only thing the old detector ever caught must now be rejected.

    An object that descends to sea level 20 m below the deck is not landing
    on it; a one-sided "at or below deck height" test would still call this
    an arrestment, so the band has a floor.
    """
    events = analyze_track(
        approach(final_altitude_m=0.0),
        None,
        carrier(),
        config=DetectionConfig(),
        deck_altitude_for=deck_altitude_for,
    )
    assert events == []


def test_an_unknown_hull_detects_nothing_rather_than_guessing() -> None:
    """A ship absent from the geometry book has no known deck height.

    Inventing one would fabricate arrestments; reporting nothing is the
    honest failure, and it is what the resolver returning None must cause.
    """
    events = analyze_track(
        approach(final_altitude_m=DECK_ALTITUDE_M),
        None,
        carrier(),
        config=DetectionConfig(),
        deck_altitude_for=lambda _c: None,
    )
    assert events == []


def test_land_detection_is_untouched_by_the_deck_path() -> None:
    """No carriers in the session: the terrain path must behave exactly as
    before, including trusting Tacview's own AGL."""
    samples = approach(final_altitude_m=0.0)
    events = analyze_track(samples, 0.0, {}, config=DetectionConfig())
    assert len(events) == 1
    assert events[0].kind == "land"
    assert events[0].touchdown.surface_is_deck is False
