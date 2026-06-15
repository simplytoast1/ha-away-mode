"""Tests for the SimulationEngine scheduling logic.

The headline case is the overnight-window regression: a window that crosses
midnight (e.g. 22:00 -> 02:00) must be recognized as currently active during
its post-midnight tail, both at the midnight recalculation and when the switch
is turned on after midnight. Before the fix the engine misclassified that tail
as "before the window" and went idle until the evening start.
"""

from __future__ import annotations

from custom_components.away_mode.const import (
    CONF_ENTITIES,
    CONF_INTENSITY,
    CONF_TIME_WINDOW_END,
    CONF_TIME_WINDOW_START,
    DOMAIN,
)
from custom_components.away_mode.simulation import SimulationEngine
from freezegun import freeze_time
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _entry(start: str, end: str, entities=None, options=None) -> MockConfigEntry:
    """Build a MockConfigEntry with a fixed-time window (no sun resolution)."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITIES: entities if entities is not None else ["light.living_room"],
            CONF_TIME_WINDOW_START: start,
            CONF_TIME_WINDOW_END: end,
            CONF_INTENSITY: "medium",
        },
        options=options or {},
    )


async def test_overnight_window_active_in_morning_tail(hass: HomeAssistant) -> None:
    """At 01:00, a 22:00 -> 02:00 window resolves as currently open."""
    await hass.config.async_set_time_zone("UTC")
    engine = SimulationEngine(hass, _entry("22:00:00", "02:00:00"))

    with freeze_time("2026-06-15 01:00:00"):
        now = dt_util.now()
        window_start, window_end = engine._resolve_window(now)

    # The window opened the previous evening and is still open right now.
    assert window_start < now < window_end
    assert window_start.hour == 22
    assert window_end.hour == 2
    # Start is anchored to yesterday, end to today.
    assert window_start.day == 14
    assert window_end.day == 15


async def test_overnight_window_before_evening_open(hass: HomeAssistant) -> None:
    """At 03:00 (after the tail), the window is in the future, not active."""
    await hass.config.async_set_time_zone("UTC")
    engine = SimulationEngine(hass, _entry("22:00:00", "02:00:00"))

    with freeze_time("2026-06-15 03:00:00"):
        now = dt_util.now()
        window_start, window_end = engine._resolve_window(now)

    assert now < window_start  # tonight's opening is still ahead
    assert window_start < window_end
    assert window_start.hour == 22
    assert window_end.hour == 2


async def test_overnight_start_schedules_events_after_midnight(
    hass: HomeAssistant,
) -> None:
    """Turning on at 01:00 inside an overnight window starts scheduling now."""
    await hass.config.async_set_time_zone("UTC")
    engine = SimulationEngine(hass, _entry("22:00:00", "02:00:00"))

    with freeze_time("2026-06-15 01:00:00"):
        engine.start()
        # State B: window-end + next-event one-shot timers are armed. Before the
        # fix this would be State A (a single start timer for 22:00) instead.
        assert engine.is_running is True
        assert engine._scheduled_callbacks
        engine.stop()

    assert engine.is_running is False
    assert engine._scheduled_callbacks == []


async def test_daytime_window_unchanged(hass: HomeAssistant) -> None:
    """A normal daytime window (18:00 -> 23:00) is returned as-is."""
    await hass.config.async_set_time_zone("UTC")
    engine = SimulationEngine(hass, _entry("18:00:00", "23:00:00"))

    with freeze_time("2026-06-15 12:00:00"):
        now = dt_util.now()
        window_start, window_end = engine._resolve_window(now)

    assert window_start.hour == 18
    assert window_end.hour == 23
    assert window_start.day == window_end.day == 15
    assert now < window_start


async def test_update_config_turns_off_only_removed_entities(
    hass: HomeAssistant,
) -> None:
    """In-place reconfigure turns off de-configured entities, keeps the rest."""
    await hass.config.async_set_time_zone("UTC")
    # data lists a+b; options (the post-edit state) lists only a.
    entry = _entry(
        "00:00:00",
        "23:59:00",
        entities=["light.a", "light.b"],
        options={CONF_ENTITIES: ["light.a"]},
    )
    engine = SimulationEngine(hass, entry)
    engine._is_running = True

    turned_off: list[str] = []
    cancelled: list[str] = []
    engine._turn_off_entity_service = lambda eid: turned_off.append(eid)
    engine._active_entities = {
        "light.a": lambda: cancelled.append("light.a"),
        "light.b": lambda: cancelled.append("light.b"),
    }

    with freeze_time("2026-06-15 12:00:00"):
        engine.update_config()

    # Only the removed entity (b) is turned off and dropped; a is untouched.
    assert turned_off == ["light.b"]
    assert cancelled == ["light.b"]
    assert "light.b" not in engine._active_entities
    assert "light.a" in engine._active_entities

    engine.stop()
