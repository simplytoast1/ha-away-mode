"""
Config flow for the Away Mode integration.

This module implements the UI-based configuration for Away Mode. It provides
two flows:

1. **ConfigFlow** (initial setup): A two-step wizard that walks the user through
   selecting entities, choosing a time window, and setting the simulation intensity.

   - Step 1 ("user"): Entity selection, start/end type (sunset/sunrise/custom),
     and intensity level.
   - Step 2 ("time_details"): Only shown if the user selected "custom" for either
     the start or end time. Presents time pickers for the custom values.

2. **OptionsFlow** (reconfiguration): Mirrors the same two-step structure so
   users can change their settings after initial setup without removing and
   re-adding the integration.

Design decisions:
  - Multiple instances are supported. Each config entry gets its own name,
    entities, time window, and intensity, so users can run several (e.g.
    "Downstairs" and "Upstairs") side by side. Storage and entity unique_ids
    are keyed by entry_id, so there are no collisions.
  - The "start_type" and "end_type" selector values ("sunset", "sunrise", "custom")
    are UI-only — the final config entry stores the resolved value (e.g., "sunset"
    or "22:30:00") in CONF_TIME_WINDOW_START / CONF_TIME_WINDOW_END.
  - We use HA's built-in EntitySelector with domain filtering so only controllable
    entity types (light, switch, media_player, fan) appear in the picker.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TimeSelector,
)

from .const import (
    CONF_CUSTOM_END_TIME,
    CONF_CUSTOM_START_TIME,
    CONF_END_TYPE,
    CONF_ENTITIES,
    CONF_INTENSITY,
    CONF_START_TYPE,
    CONF_TIME_WINDOW_END,
    CONF_TIME_WINDOW_START,
    DEFAULT_INTENSITY,
    DEFAULT_NAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# =============================================================================
# Selector options for the time window start/end type dropdowns.
# These are the choices the user sees in the UI for when the simulation
# window begins and ends. "sunset" and "sunrise" are resolved dynamically
# each day using Home Assistant's sun data. "custom" shows a time picker.
# =============================================================================
TIME_TYPE_OPTIONS = [
    {"value": "sunset", "label": "Sunset"},
    {"value": "sunrise", "label": "Sunrise"},
    {"value": "custom", "label": "Custom time"},
]

# =============================================================================
# Selector options for the intensity level dropdown.
# Each level maps to an intensity profile defined in const.py that controls
# how active the simulation appears.
# =============================================================================
INTENSITY_OPTIONS = [
    {"value": "low", "label": "Low — Subtle, occasional activity (1-2 lights)"},
    {"value": "medium", "label": "Medium — Natural evening occupancy"},
    {"value": "high", "label": "High — Busy household, frequent changes"},
]


def _build_user_schema(
    defaults: dict[str, Any] | None = None,
    include_name: bool = True,
) -> vol.Schema:
    """
    Build the schema for the main configuration step (step 1).

    This schema defines the form fields the user sees when first setting up
    Away Mode or when opening the options flow. It includes:
      - An optional name (initial setup only) used as the instance/device name
      - An entity multi-selector filtered to only show lights, switches,
        media players, and fans (the entity types we can meaningfully simulate)
      - A dropdown for the simulation start type (sunset/sunrise/custom)
      - A dropdown for the simulation end type (sunset/sunrise/custom)
      - A dropdown for the intensity level (low/medium/high)

    Args:
        defaults: Optional dictionary of default values to pre-fill the form
                  fields. Used by the options flow to show current settings.
        include_name: Whether to include the name field. True for initial
                      setup (the name is part of the instance's identity);
                      False for the options flow, where the name lives in
                      entry.data and is not edited as a runtime option.

    Returns:
        A voluptuous Schema for the user step form.
    """
    # If no defaults are provided, use sensible initial values.
    defaults = defaults or {}

    schema_dict: dict[Any, Any] = {}

    # Name field: only on initial setup. Lets users distinguish multiple
    # Away Mode instances (e.g. "Downstairs", "Upstairs").
    if include_name:
        schema_dict[
            vol.Optional(
                CONF_NAME,
                default=defaults.get(CONF_NAME, DEFAULT_NAME),
            )
        ] = TextSelector()

    schema_dict.update(
        {
            # Entity selector: allows multiple selections, filtered to only
            # show entity domains that can be turned on/off meaningfully.
            vol.Required(
                CONF_ENTITIES,
                default=defaults.get(CONF_ENTITIES, []),
            ): EntitySelector(
                EntitySelectorConfig(
                    # Only show entities from these domains in the picker.
                    # These are the entity types that make sense for presence
                    # simulation — they can be turned on and off.
                    domain=["light", "switch", "media_player", "fan"],
                    multiple=True,
                ),
            ),
            # Start type dropdown: determines when the simulation window opens.
            # If "custom" is selected, a time picker will be shown in step 2.
            vol.Required(
                CONF_START_TYPE,
                default=defaults.get(CONF_START_TYPE, "sunset"),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=TIME_TYPE_OPTIONS,
                    mode=SelectSelectorMode.DROPDOWN,
                ),
            ),
            # End type dropdown: determines when the simulation window closes.
            # If "custom" is selected, a time picker will be shown in step 2.
            vol.Required(
                CONF_END_TYPE,
                default=defaults.get(CONF_END_TYPE, "custom"),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=TIME_TYPE_OPTIONS,
                    mode=SelectSelectorMode.DROPDOWN,
                ),
            ),
            # Intensity selector: controls how "busy" the simulation appears.
            # Maps to profiles in const.py that define timing parameters.
            vol.Required(
                CONF_INTENSITY,
                default=defaults.get(CONF_INTENSITY, DEFAULT_INTENSITY),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=INTENSITY_OPTIONS,
                    mode=SelectSelectorMode.DROPDOWN,
                ),
            ),
        }
    )

    return vol.Schema(schema_dict)


def _build_time_details_schema(
    show_start: bool = False,
    show_end: bool = False,
    defaults: dict[str, Any] | None = None,
) -> vol.Schema:
    """
    Build the schema for the custom time details step (step 2).

    This step is only shown when the user selected "custom" for the start
    and/or end time type in step 1. It presents time picker(s) for the
    custom time value(s).

    Args:
        show_start: Whether to show the custom start time picker.
                    True when start_type == "custom".
        show_end: Whether to show the custom end time picker.
                  True when end_type == "custom".
        defaults: Optional dictionary of default values for pre-filling.

    Returns:
        A voluptuous Schema containing only the needed time picker field(s).
    """
    defaults = defaults or {}
    schema_dict = {}

    if show_start:
        # Time picker for custom start time.
        # Default to 18:00:00 (6 PM) as a sensible evening start if no
        # previous value exists.
        schema_dict[vol.Required(
            CONF_CUSTOM_START_TIME,
            default=defaults.get(CONF_CUSTOM_START_TIME, "18:00:00"),
        )] = TimeSelector()

    if show_end:
        # Time picker for custom end time.
        # Default to 23:00:00 (11 PM) as a sensible bedtime if no
        # previous value exists.
        schema_dict[vol.Required(
            CONF_CUSTOM_END_TIME,
            default=defaults.get(CONF_CUSTOM_END_TIME, "23:00:00"),
        )] = TimeSelector()

    return vol.Schema(schema_dict)


def _resolve_time_config(user_input: dict[str, Any]) -> dict[str, Any]:
    """
    Convert the UI form values into the final config entry data.

    The config flow UI uses separate "type" selectors (sunset/sunrise/custom)
    and time pickers, but the final config entry should store a single value
    for start and end:
      - "sunset" or "sunrise" if those were selected
      - A time string like "18:30:00" if "custom" was selected

    This function merges the step 1 and step 2 data into the final format.

    Args:
        user_input: Combined dictionary of all user inputs from both steps.

    Returns:
        Dictionary with the final config entry keys and values, ready to be
        stored in the ConfigEntry.data.
    """
    # Determine the start time value: either a keyword or a custom time string.
    start_type = user_input[CONF_START_TYPE]
    if start_type == "custom":
        # User selected a custom time — use the time picker value.
        time_start = user_input[CONF_CUSTOM_START_TIME]
    else:
        # User selected "sunset" or "sunrise" — store the keyword directly.
        # The simulation engine will resolve this to a concrete time each day.
        time_start = start_type

    # Same logic for the end time.
    end_type = user_input[CONF_END_TYPE]
    if end_type == "custom":
        time_end = user_input[CONF_CUSTOM_END_TIME]
    else:
        time_end = end_type

    return {
        CONF_ENTITIES: user_input[CONF_ENTITIES],
        CONF_TIME_WINDOW_START: time_start,
        CONF_TIME_WINDOW_END: time_end,
        CONF_INTENSITY: user_input[CONF_INTENSITY],
    }


# =============================================================================
# ConfigFlow: Initial setup when the user adds the integration
# =============================================================================


class AwayModeConfigFlow(ConfigFlow, domain=DOMAIN):
    """
    Handle the initial configuration flow for Away Mode.

    This flow is triggered when the user goes to:
      Settings -> Integrations -> Add Integration -> "Away Mode"

    It walks through two steps:
      1. Select entities, time window type, and intensity
      2. (Conditional) Set custom time values if "custom" was selected

    Only one instance of Away Mode is allowed — the flow will abort if
    the integration is already configured.
    """

    # Schema version for future migration support.
    # If we ever need to change the config entry structure, we can
    # increment this and add a migration handler.
    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow with empty state for multi-step data."""
        # _user_input stores the data from step 1 so it can be combined
        # with step 2 data before creating the config entry.
        self._user_input: dict[str, Any] = {}

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """
        Handle step 1: Entity selection, time window type, and intensity.

        This is the entry point of the config flow. On first load (user_input
        is None), it shows the form. On submission (user_input has data), it
        validates the input and either moves to step 2 (if custom times are
        needed) or creates the config entry directly.

        Args:
            user_input: The form data submitted by the user, or None on first load.

        Returns:
            FlowResult: Either a form to display, an abort, or a created entry.
        """
        # Multiple instances are supported: storage (hass.data[DOMAIN][entry_id])
        # and entity unique_ids are all keyed by entry_id, so users can run
        # several Away Mode configs (e.g. "Downstairs", "Upstairs") side by side.

        # Track validation errors to display to the user.
        errors: dict[str, str] = {}

        if user_input is not None:
            # --- Validation ---

            # At least one entity must be selected for the simulation to do anything.
            if not user_input.get(CONF_ENTITIES):
                errors["base"] = "no_entities"

            if not errors:
                # Store step 1 data for later combination with step 2.
                self._user_input = user_input

                # Check if we need to show the custom time pickers (step 2).
                # If both start and end are sunset/sunrise, we can skip step 2
                # entirely and create the config entry now.
                needs_custom_start = user_input[CONF_START_TYPE] == "custom"
                needs_custom_end = user_input[CONF_END_TYPE] == "custom"

                if needs_custom_start or needs_custom_end:
                    # At least one custom time is needed — show step 2.
                    return await self.async_step_time_details()

                # No custom times needed — resolve and create the entry directly.
                data = _resolve_time_config(user_input)
                data[CONF_NAME] = user_input.get(CONF_NAME, DEFAULT_NAME)
                return self.async_create_entry(title=data[CONF_NAME], data=data)

        # Show the form (either first load or after validation errors).
        return self.async_show_form(
            step_id="user",
            data_schema=_build_user_schema(),
            errors=errors,
        )

    async def async_step_time_details(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """
        Handle step 2: Custom time picker(s).

        This step is only reached if the user selected "custom" for either
        the start or end time in step 1. It shows time picker(s) for the
        custom value(s) and then creates the config entry.

        Args:
            user_input: The time picker data submitted by the user, or None
                        on first load.

        Returns:
            FlowResult: Either a form to display or a created entry.
        """
        # Determine which time pickers to show based on step 1 selections.
        needs_custom_start = self._user_input[CONF_START_TYPE] == "custom"
        needs_custom_end = self._user_input[CONF_END_TYPE] == "custom"

        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate that required custom times are provided.
            if needs_custom_start and not user_input.get(CONF_CUSTOM_START_TIME):
                errors["base"] = "missing_start_time"
            if needs_custom_end and not user_input.get(CONF_CUSTOM_END_TIME):
                errors["base"] = "missing_end_time"

            if not errors:
                # Merge step 1 and step 2 data, resolve to final config format,
                # and create the config entry.
                combined = {**self._user_input, **user_input}
                data = _resolve_time_config(combined)
                data[CONF_NAME] = combined.get(CONF_NAME, DEFAULT_NAME)
                return self.async_create_entry(title=data[CONF_NAME], data=data)

        # Show the time picker form.
        return self.async_show_form(
            step_id="time_details",
            data_schema=_build_time_details_schema(
                show_start=needs_custom_start,
                show_end=needs_custom_end,
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> AwayModeOptionsFlow:
        """
        Return the options flow handler for this integration.

        This is called by Home Assistant when the user clicks "Configure"
        on an existing Away Mode integration entry. It returns an instance
        of the OptionsFlow that mirrors our ConfigFlow steps.

        Args:
            config_entry: The existing config entry being reconfigured.

        Returns:
            An instance of AwayModeOptionsFlow.
        """
        return AwayModeOptionsFlow(config_entry)


# =============================================================================
# OptionsFlow: Reconfiguration after initial setup
# =============================================================================


class AwayModeOptionsFlow(OptionsFlow):
    """
    Handle the options flow for reconfiguring Away Mode.

    This flow is triggered when the user clicks "Configure" on an existing
    Away Mode integration entry in Settings -> Integrations. It mirrors
    the same two-step structure as the ConfigFlow but pre-fills form fields
    with the current configuration values.

    When the user saves new options, the integration entry is updated and
    the integration is automatically reloaded (via async_update_reload_and_abort),
    which restarts the simulation engine with the new settings.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        """
        Initialize the options flow with the current config entry.

        We extract the current settings to use as default values in the
        form fields, so the user sees their existing configuration and
        can modify only what they want to change.

        Args:
            config_entry: The existing config entry with current settings.
        """
        self._config_entry = config_entry
        # Store step 1 data for combining with step 2 (same pattern as ConfigFlow).
        self._user_input: dict[str, Any] = {}

        # Read the current settings from the merged data/options view so the
        # form pre-fills with whatever is in effect (options override data).
        merged = {**config_entry.data, **config_entry.options}

        # Determine the current start/end types for pre-filling the dropdowns.
        # If the stored value is "sunset" or "sunrise", the type is that keyword.
        # Otherwise, it's a custom time string.
        current_start = merged.get(CONF_TIME_WINDOW_START, "sunset")
        current_end = merged.get(CONF_TIME_WINDOW_END, "23:00:00")

        # Build defaults dict for pre-filling form fields.
        self._defaults: dict[str, Any] = {
            CONF_ENTITIES: merged.get(CONF_ENTITIES, []),
            CONF_START_TYPE: (
                current_start
                if current_start in ("sunset", "sunrise")
                else "custom"
            ),
            CONF_END_TYPE: (
                current_end if current_end in ("sunset", "sunrise") else "custom"
            ),
            CONF_INTENSITY: merged.get(CONF_INTENSITY, DEFAULT_INTENSITY),
        }

        # If the current values are custom times, store them for the pickers.
        if current_start not in ("sunset", "sunrise"):
            self._defaults[CONF_CUSTOM_START_TIME] = current_start
        if current_end not in ("sunset", "sunrise"):
            self._defaults[CONF_CUSTOM_END_TIME] = current_end

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """
        Handle the first step of the options flow.

        This mirrors async_step_user from the ConfigFlow but uses the
        "init" step ID (required by HA for options flows) and pre-fills
        fields with current settings.

        Args:
            user_input: The form data submitted by the user, or None on first load.

        Returns:
            FlowResult: Either a form, a redirect to step 2, or a completed entry.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate: at least one entity must be selected.
            if not user_input.get(CONF_ENTITIES):
                errors["base"] = "no_entities"

            if not errors:
                self._user_input = user_input

                # Check if we need custom time pickers.
                needs_custom_start = user_input[CONF_START_TYPE] == "custom"
                needs_custom_end = user_input[CONF_END_TYPE] == "custom"

                if needs_custom_start or needs_custom_end:
                    return await self.async_step_time_details()

                # No custom times — save the tunable settings to entry.options.
                # The engine reads {**entry.data, **entry.options}, and the
                # update listener applies the change in place (no reload).
                return self.async_create_entry(
                    title="", data=_resolve_time_config(user_input)
                )

        # Show the form with current settings as defaults. The name is not an
        # editable option (it lives in entry.data), so hide it here.
        return self.async_show_form(
            step_id="init",
            data_schema=_build_user_schema(
                defaults=self._defaults, include_name=False
            ),
            errors=errors,
        )

    async def async_step_time_details(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """
        Handle step 2 of the options flow: custom time picker(s).

        Mirrors the ConfigFlow's time_details step but in the options context.
        Pre-fills with current custom time values if they exist.

        Args:
            user_input: The time picker data, or None on first load.

        Returns:
            FlowResult: Either a form or the updated entry.
        """
        needs_custom_start = self._user_input[CONF_START_TYPE] == "custom"
        needs_custom_end = self._user_input[CONF_END_TYPE] == "custom"

        errors: dict[str, str] = {}

        if user_input is not None:
            if needs_custom_start and not user_input.get(CONF_CUSTOM_START_TIME):
                errors["base"] = "missing_start_time"
            if needs_custom_end and not user_input.get(CONF_CUSTOM_END_TIME):
                errors["base"] = "missing_end_time"

            if not errors:
                combined = {**self._user_input, **user_input}
                # Save the tunable settings to entry.options (see async_step_init).
                return self.async_create_entry(
                    title="", data=_resolve_time_config(combined)
                )

        # Show time pickers with current values as defaults.
        return self.async_show_form(
            step_id="time_details",
            data_schema=_build_time_details_schema(
                show_start=needs_custom_start,
                show_end=needs_custom_end,
                defaults=self._defaults,
            ),
            errors=errors,
        )
