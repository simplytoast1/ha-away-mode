"""
Away Mode integration for Home Assistant.

This is the main entry point for the Away Mode integration. It handles the
integration lifecycle:
  - Setting up the integration when a config entry is created (async_setup_entry)
  - Tearing down the integration when a config entry is removed (async_unload_entry)

Architecture overview:
  The integration consists of three main components:

  1. **SimulationEngine** (simulation.py): The brain that schedules entity
     on/off events. Created here and stored in hass.data so other components
     can access it.

  2. **AwayModeSwitch** (switch.py): A switch entity that the user toggles
     to start/stop the simulation. It accesses the SimulationEngine from
     hass.data.

  3. **ConfigFlow** (config_flow.py): The UI configuration that stores
     user settings in the ConfigEntry.

Data flow:
  ConfigEntry (user settings)
       ↓
  SimulationEngine (created here, stored in hass.data)
       ↓
  AwayModeSwitch (reads engine from hass.data, controls start/stop)

No services are registered by this integration. Users control the simulation
entirely through the switch entity, which can be toggled via:
  - The HA UI (clicking the switch)
  - Automations (switch.turn_on / switch.turn_off service calls)
  - Scripts, scenes, or voice assistants

This is simpler and more consistent with HA patterns than registering
custom services (which is what presence_simulation does).
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .simulation import SimulationEngine

_LOGGER = logging.getLogger(__name__)

# The list of HA platforms this integration provides.
# We only provide a switch platform — the on/off toggle for the simulation.
PLATFORMS: list[Platform] = [Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Set up Away Mode from a config entry.

    This is called by Home Assistant when:
      - The user completes the config flow (initial setup)
      - Home Assistant starts up and loads previously configured entries
      - The integration is reloaded (e.g., after options flow changes)

    We create the SimulationEngine instance and store it in hass.data so
    the switch entity (set up via platform forwarding) can access it.

    Note: The engine is created but NOT started here. It only starts when
    the switch entity is turned on (or when RestoreEntity recovers a
    previous "on" state after restart).

    Args:
        hass: The Home Assistant instance.
        entry: The config entry containing user settings (entities, time
               window, intensity).

    Returns:
        True if setup succeeded, False otherwise.
    """
    _LOGGER.info("Setting up Away Mode integration (entry: %s)", entry.entry_id)

    # Create the simulation engine with the user's configuration.
    # The engine is initialized but idle — it won't start scheduling
    # until the switch entity calls engine.start().
    engine = SimulationEngine(hass, entry)

    # Store the engine in hass.data so the switch platform can access it.
    # We use a nested dict: hass.data[DOMAIN][entry.entry_id] = engine
    # This pattern supports multiple config entries (though we currently
    # limit to one via the config flow).
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = engine

    # Forward setup to the switch platform.
    # This causes HA to call switch.py's async_setup_entry(), which
    # creates the AwayModeSwitch entity.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register a listener for options flow updates.
    # When the user changes settings via the options flow, this triggers
    # a reload of the integration, which calls async_unload_entry followed
    # by async_setup_entry with the updated config.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _LOGGER.info("Away Mode integration setup complete")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Unload an Away Mode config entry.

    This is called by Home Assistant when:
      - The user removes the integration
      - The integration is being reloaded (after options change)
      - Home Assistant is shutting down

    We stop the simulation engine (which turns off all active entities)
    and clean up our data from hass.data.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being unloaded.

    Returns:
        True if unloading succeeded, False otherwise.
    """
    _LOGGER.info("Unloading Away Mode integration (entry: %s)", entry.entry_id)

    # Stop the simulation engine.
    # This cancels all scheduled callbacks and turns off any currently
    # active entities, so lights don't stay on after the integration
    # is removed or HA shuts down.
    domain_data = hass.data.get(DOMAIN, {})
    engine = domain_data.pop(entry.entry_id, None)
    if engine is not None:
        engine.stop()

    # Unload the switch platform.
    # This removes the AwayModeSwitch entity from HA's entity registry.
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Clean up the domain data dict if no entries remain.
    if DOMAIN in hass.data and not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)

    _LOGGER.info("Away Mode integration unloaded (success: %s)", unload_ok)
    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """
    Handle options flow updates by reloading the integration.

    When the user changes settings via the options flow (e.g., adds/removes
    entities, changes the time window, or adjusts intensity), this listener
    triggers a full reload of the integration. This is simpler and more
    reliable than trying to hot-patch a running simulation engine.

    The reload process:
      1. async_unload_entry is called (stops engine, cleans up)
      2. async_setup_entry is called with updated config (creates fresh engine)
      3. The switch entity is recreated and may restore its previous state

    Args:
        hass: The Home Assistant instance.
        entry: The config entry that was updated.
    """
    _LOGGER.info("Away Mode configuration updated, reloading integration")
    await hass.config_entries.async_reload(entry.entry_id)
