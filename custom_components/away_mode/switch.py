"""
Switch entity for the Away Mode integration.

This module provides the AwayModeSwitch entity — a simple on/off switch
that controls the simulation engine. When turned on, the simulation begins
scheduling random entity activations within the configured time window.
When turned off, all active entities are turned off and scheduling stops.

Design decisions:

  - **Switch (not a service)**: We use a standard switch entity rather than
    registering custom services (start/stop/toggle). This is more consistent
    with HA patterns and means users can control the simulation through any
    mechanism that can toggle a switch: the UI, automations, scripts, scenes,
    voice assistants, etc. There's no need to learn custom service names.

  - **RestoreEntity**: The switch extends RestoreEntity so its state survives
    Home Assistant restarts. If the user turned on Away Mode and HA restarts
    (e.g., after an update), the switch automatically restores to "on" and
    re-starts the simulation engine. Without this, HA restarts would silently
    disable the simulation, defeating its purpose.

  - **One switch per entry**: Each config entry creates exactly one switch,
    grouped under a per-entry service device. Multiple entries are supported,
    so there can be several Away Mode switches (one per configured instance),
    each with its own name.

Usage examples:

  In an automation (enable when nobody is home):
    trigger:
      - platform: state
        entity_id: group.family
        to: "not_home"
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.away_mode

  In the UI: Just click the switch in the dashboard.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DEFAULT_NAME, DOMAIN
from .simulation import SimulationEngine

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Set up the Away Mode switch entity from a config entry.

    This function is called by Home Assistant's platform forwarding system
    when the integration is set up (triggered by async_forward_entry_setups
    in __init__.py). It retrieves the SimulationEngine from hass.data and
    creates the switch entity.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry containing user settings.
        async_add_entities: Callback to register new entities with HA.
    """
    # Retrieve the simulation engine that was created in __init__.py's
    # async_setup_entry and stored in hass.data.
    engine: SimulationEngine = hass.data[DOMAIN][entry.entry_id]

    # Create and register the switch entity.
    # update_before_add=False because the switch state comes from the engine,
    # not from polling an external device.
    async_add_entities([AwayModeSwitch(engine, entry)], update_before_add=False)

    _LOGGER.debug("Away Mode switch entity registered")


class AwayModeSwitch(SwitchEntity, RestoreEntity):
    """
    A switch entity that controls the Away Mode simulation.

    This switch serves as the single control point for the presence simulation.
    Turning it on starts the simulation engine, which begins scheduling random
    entity activations within the configured time window. Turning it off stops
    the engine and turns off all currently simulated entities.

    The switch uses HA's RestoreEntity mixin to persist its state across
    Home Assistant restarts. If the switch was "on" before a restart, it
    automatically re-starts the simulation engine during initialization.

    Attributes:
        _engine: Reference to the SimulationEngine that does the actual work.
        _entry: The config entry with user settings.
        _attr_has_entity_name: Tells HA this entity uses the "entity name"
            pattern rather than a fully qualified name.
    """

    # Tell HA that this entity provides its own name (not a device name prefix).
    _attr_has_entity_name = True

    def __init__(self, engine: SimulationEngine, entry: ConfigEntry) -> None:
        """
        Initialize the Away Mode switch.

        The switch starts in an "off" state. If RestoreEntity finds a previous
        "on" state during async_added_to_hass, it will start the engine.

        Args:
            engine: The SimulationEngine instance to control.
            entry: The config entry, used for unique_id generation.
        """
        self._engine = engine
        self._entry = entry

        # Unique ID for this entity within HA's entity registry.
        # Based on the config entry ID to ensure uniqueness even if the
        # integration is removed and re-added.
        self._attr_unique_id = f"{entry.entry_id}_away_mode"

        # The instance name comes from the merged config (options override data).
        name = {**entry.data, **entry.options}.get(CONF_NAME, DEFAULT_NAME)

        # Group this entity under a virtual service device so it isn't an
        # orphan in the dashboard, and so multiple instances appear as
        # separate, distinctly named devices.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=name,
            manufacturer="Away Mode",
            model="Presence Simulator",
            entry_type=DeviceEntryType.SERVICE,
        )

        # With has_entity_name set and a device present, leaving the entity
        # name as None makes the switch adopt the device name (the configured
        # instance name), e.g. "Away Mode" -> switch.away_mode.
        self._attr_name = None

        # Initial icon (will be overridden by the icon property).
        self._attr_icon = "mdi:home-account"

    @property
    def icon(self) -> str:
        """
        Return the icon based on the current switch state.

        Shows different icons so the user can visually distinguish between:
          - Off (mdi:home-account): Normal state, no simulation running
          - On (mdi:home-clock): Simulation active, home is being "watched"

        Returns:
            The MDI icon string for the current state.
        """
        if self.is_on:
            # Clock icon suggests the simulation is actively managing
            # the home on a schedule.
            return "mdi:home-clock"
        # Account icon suggests the normal "someone is home" state.
        return "mdi:home-account"

    @property
    def is_on(self) -> bool:
        """
        Return whether the simulation is currently running.

        We delegate to the engine's is_running property rather than
        maintaining a separate state flag. This ensures the switch
        state always accurately reflects the engine's actual state.

        Returns:
            True if the simulation engine is running, False otherwise.
        """
        return self._engine.is_running

    async def async_turn_on(self, **kwargs: Any) -> None:
        """
        Turn on the Away Mode simulation.

        Called when:
          - The user toggles the switch on in the UI
          - An automation calls switch.turn_on targeting this entity
          - A script or scene activates this switch

        Starts the simulation engine, which will begin scheduling entity
        activations within the configured time window.

        Args:
            **kwargs: Additional keyword arguments (unused, required by HA API).
        """
        _LOGGER.info("Away Mode switch turned ON")
        self._engine.start()

        # Notify HA that our state has changed so the UI updates immediately.
        # Without this, the UI might not reflect the change until the next
        # scheduled state poll.
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """
        Turn off the Away Mode simulation.

        Called when:
          - The user toggles the switch off in the UI
          - An automation calls switch.turn_off targeting this entity
          - A script or scene deactivates this switch

        Stops the simulation engine, which:
          1. Cancels all scheduled future events
          2. Turns off all currently active (simulated) entities
          3. Clears all internal state

        Args:
            **kwargs: Additional keyword arguments (unused, required by HA API).
        """
        _LOGGER.info("Away Mode switch turned OFF")
        self._engine.stop()

        # Notify HA of the state change.
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """
        Called when the entity is added to Home Assistant.

        This lifecycle method is called after the entity is fully registered
        with HA. We use it to restore the previous state after an HA restart.

        The RestoreEntity mixin provides async_get_last_state(), which returns
        the entity's state from before the restart (stored in HA's state
        machine persistence). If the switch was "on" before the restart, we
        re-start the simulation engine so the presence simulation continues
        seamlessly.

        This is critical for the integration's usefulness: if a user turns on
        Away Mode before leaving for vacation, they expect it to survive HA
        updates and restarts without manual intervention.
        """
        await super().async_added_to_hass()

        # Check if there's a previous state to restore.
        last_state = await self.async_get_last_state()

        if last_state is not None and last_state.state == "on":
            # The switch was on before the restart — re-start the engine.
            _LOGGER.info(
                "Restoring Away Mode switch to ON state after restart"
            )
            self._engine.start()
        else:
            _LOGGER.debug(
                "No previous ON state to restore (last state: %s)",
                last_state.state if last_state else "None",
            )
