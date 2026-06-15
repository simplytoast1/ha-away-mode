"""Tests for the Away Mode config and options flows."""

from __future__ import annotations

from custom_components.away_mode.const import (
    CONF_ENTITIES,
    CONF_INTENSITY,
    CONF_TIME_WINDOW_END,
    CONF_TIME_WINDOW_START,
    DOMAIN,
)
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_user_flow_sunset_sunrise(hass: HomeAssistant) -> None:
    """The sunset/sunrise path creates the entry in a single step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Away Mode",
            CONF_ENTITIES: ["light.living_room"],
            "start_type": "sunset",
            "end_type": "sunrise",
            CONF_INTENSITY: "medium",
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Away Mode"
    assert result["data"] == {
        CONF_NAME: "Away Mode",
        CONF_ENTITIES: ["light.living_room"],
        CONF_TIME_WINDOW_START: "sunset",
        CONF_TIME_WINDOW_END: "sunrise",
        CONF_INTENSITY: "medium",
    }


async def test_user_flow_custom_times_two_steps(hass: HomeAssistant) -> None:
    """A custom start/end routes through the second time-picker step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Night",
            CONF_ENTITIES: ["light.living_room"],
            "start_type": "custom",
            "end_type": "custom",
            CONF_INTENSITY: "low",
        },
    )

    # Custom times -> a second form, not an entry yet.
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "time_details"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"custom_start_time": "22:00:00", "custom_end_time": "02:00:00"},
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Night"
    assert result["data"][CONF_NAME] == "Night"
    assert result["data"][CONF_TIME_WINDOW_START] == "22:00:00"
    assert result["data"][CONF_TIME_WINDOW_END] == "02:00:00"


async def test_user_flow_requires_entity(hass: HomeAssistant) -> None:
    """Submitting with no entities re-shows the form with an error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Away Mode",
            CONF_ENTITIES: [],
            "start_type": "sunset",
            "end_type": "sunrise",
            CONF_INTENSITY: "medium",
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "no_entities"}


async def test_multiple_instances_allowed(hass: HomeAssistant) -> None:
    """A second instance can be configured (no single-instance abort)."""
    existing = MockConfigEntry(
        domain=DOMAIN,
        title="First",
        data={
            CONF_NAME: "First",
            CONF_ENTITIES: ["light.living_room"],
            CONF_TIME_WINDOW_START: "sunset",
            CONF_TIME_WINDOW_END: "sunrise",
            CONF_INTENSITY: "medium",
        },
    )
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Second",
            CONF_ENTITIES: ["light.kitchen"],
            "start_type": "sunset",
            "end_type": "sunrise",
            CONF_INTENSITY: "medium",
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Second"


async def test_options_flow_writes_options(hass: HomeAssistant) -> None:
    """The options flow stores tunables in entry.options, not entry.data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Away Mode",
        data={
            CONF_NAME: "Away Mode",
            CONF_ENTITIES: ["light.living_room"],
            CONF_TIME_WINDOW_START: "sunset",
            CONF_TIME_WINDOW_END: "sunrise",
            CONF_INTENSITY: "medium",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ENTITIES: ["light.living_room", "light.kitchen"],
            "start_type": "sunset",
            "end_type": "sunrise",
            CONF_INTENSITY: "high",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    # Tunables landed in options; original data is untouched.
    assert entry.options[CONF_INTENSITY] == "high"
    assert entry.options[CONF_ENTITIES] == ["light.living_room", "light.kitchen"]
    assert entry.data[CONF_INTENSITY] == "medium"
    # The merged view (what the engine reads) reflects the new values.
    merged = {**entry.data, **entry.options}
    assert merged[CONF_INTENSITY] == "high"
