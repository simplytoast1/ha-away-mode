"""
Constants for the Away Mode integration.

This module defines all constant values used throughout the Away Mode integration,
including the domain identifier, configuration keys, default values, and intensity
profiles that control the simulation behavior.

The intensity profiles are the heart of the simulation's "personality" — they
determine how active the house appears by controlling:
  - How many entities can be on simultaneously (as a fraction of total)
  - How long each entity stays on (min/max range in minutes)
  - How long the gap is between new entity activations (min/max range in minutes)

These profiles were tuned to produce realistic patterns:
  - "low" mimics someone who is mostly in one room, occasionally moving
  - "medium" mimics typical evening activity — kitchen, living room, bedroom
  - "high" mimics a busy household with frequent room changes
"""

# =============================================================================
# Domain
# =============================================================================

# The unique identifier for this integration within Home Assistant.
# Used as the key in hass.data, service names, and entity platform registration.
DOMAIN = "away_mode"

# =============================================================================
# Configuration Keys
# =============================================================================

# Key for the list of entity IDs the user wants to include in the simulation.
# Stored as a list of strings, e.g., ["light.living_room", "switch.kitchen"].
CONF_ENTITIES = "entities"

# Key for when the simulation time window starts.
# Can be "sunset", "sunrise", or a time string like "18:30:00".
CONF_TIME_WINDOW_START = "time_window_start"

# Key for when the simulation time window ends.
# Can be "sunset", "sunrise", or a time string like "23:00:00".
CONF_TIME_WINDOW_END = "time_window_end"

# Key for the simulation intensity level.
# Must be one of: "low", "medium", "high".
CONF_INTENSITY = "intensity"

# Key for the start type selector in the config flow.
# This is a UI-only value ("sunset", "sunrise", or "custom") that determines
# whether to show a time picker. It is NOT stored in the final config entry.
CONF_START_TYPE = "start_type"

# Key for the end type selector in the config flow.
# Same as CONF_START_TYPE but for the end of the time window.
CONF_END_TYPE = "end_type"

# Key for the custom start time picker value (HH:MM:SS format).
# Only used when start_type is "custom".
CONF_CUSTOM_START_TIME = "custom_start_time"

# Key for the custom end time picker value (HH:MM:SS format).
# Only used when end_type is "custom".
CONF_CUSTOM_END_TIME = "custom_end_time"

# =============================================================================
# Defaults
# =============================================================================

# The default intensity level if none is specified.
# "medium" provides a good balance of realism without being too active or too quiet.
DEFAULT_INTENSITY = "medium"

# The default name for an Away Mode instance. Used as the config entry title,
# the service-device name, and the switch name when the user doesn't provide one.
# Stored under homeassistant.const.CONF_NAME ("name") in entry.data.
DEFAULT_NAME = "Away Mode"

# =============================================================================
# Intensity Profiles
# =============================================================================

# Each profile is a dictionary with the following keys:
#
#   max_simultaneous (float): The maximum fraction of configured entities that
#       can be turned on at the same time. For example, 0.35 means at most 35%
#       of entities will be on simultaneously. This prevents the unrealistic
#       scenario of every light in the house being on at once.
#
#   on_min (int): The minimum number of minutes an entity stays on once activated.
#       This prevents unnaturally short flickers (e.g., a light on for 2 minutes
#       looks like a timer, not a person).
#
#   on_max (int): The maximum number of minutes an entity stays on. This prevents
#       entities from staying on for hours, which would look like someone left
#       a light on rather than actively using the house.
#
#   gap_min (int): The minimum number of minutes between new entity activations.
#       This creates breathing room between events so it doesn't look like a
#       programmed sequence firing off rapidly.
#
#   gap_max (int): The maximum number of minutes between activations. This caps
#       how long the house can sit "idle" before the next activity, ensuring
#       the simulation doesn't go quiet for too long during the active window.

INTENSITY_PROFILES = {
    "low": {
        "max_simultaneous": 0.2,   # At most 20% of entities on at once
        "on_min": 20,              # Entity stays on for at least 20 minutes
        "on_max": 60,              # Entity stays on for at most 60 minutes
        "gap_min": 10,             # At least 10 minutes between new activations
        "gap_max": 30,             # At most 30 minutes between new activations
    },
    "medium": {
        "max_simultaneous": 0.35,  # At most 35% of entities on at once
        "on_min": 15,              # Entity stays on for at least 15 minutes
        "on_max": 45,              # Entity stays on for at most 45 minutes
        "gap_min": 5,              # At least 5 minutes between new activations
        "gap_max": 20,             # At most 20 minutes between new activations
    },
    "high": {
        "max_simultaneous": 0.5,   # At most 50% of entities on at once
        "on_min": 10,              # Entity stays on for at least 10 minutes
        "on_max": 35,              # Entity stays on for at most 35 minutes
        "gap_min": 3,              # At least 3 minutes between new activations
        "gap_max": 15,             # At most 15 minutes between new activations
    },
}

# =============================================================================
# Platform Registration
# =============================================================================

# The list of HA platforms this integration provides entities for.
# We only provide a switch platform (the on/off toggle for the simulation).
PLATFORMS = ["switch"]
