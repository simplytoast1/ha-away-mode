"""Tests for the Away Mode switch entity and its lifecycle."""

from __future__ import annotations

from custom_components.away_mode.const import (
    CONF_ENTITIES,
    CONF_INTENSITY,
    CONF_TIME_WINDOW_END,
    CONF_TIME_WINDOW_START,
    DOMAIN,
)
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

# A round-the-clock fixed window so setup lands "inside" the window without
# needing sun resolution, and start/stop is deterministic.
ENTITY_ID = "switch.away_mode"


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Away Mode",
        data={
            "name": "Away Mode",
            CONF_ENTITIES: ["light.living_room"],
            CONF_TIME_WINDOW_START: "00:00:00",
            CONF_TIME_WINDOW_END: "23:59:00",
            CONF_INTENSITY: "low",
        },
    )


async def test_switch_setup_toggle_and_device(hass: HomeAssistant) -> None:
    """Setup registers an off switch grouped under a service device."""
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_OFF

    # The entity is attached to a service device (not an orphan).
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device is not None
    assert device.name == "Away Mode"

    engine = hass.data[DOMAIN][entry.entry_id]

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": ENTITY_ID}, blocking=True
    )
    assert engine.is_running is True

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": ENTITY_ID}, blocking=True
    )
    assert engine.is_running is False


async def test_switch_restores_on_state(hass: HomeAssistant) -> None:
    """A switch that was ON before restart re-starts the engine."""
    mock_restore_cache(hass, [State(ENTITY_ID, STATE_ON)])

    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    engine = hass.data[DOMAIN][entry.entry_id]
    assert engine.is_running is True

    # Clean up so no timers linger past the test.
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": ENTITY_ID}, blocking=True
    )
