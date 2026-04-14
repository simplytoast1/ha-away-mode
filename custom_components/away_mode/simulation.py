"""
Core simulation engine for the Away Mode integration.

This is the heart of the Away Mode integration. The SimulationEngine class
manages all scheduling logic that makes a home look occupied while the
residents are away. It works by turning configured entities (lights, switches,
fans, media players) on and off at randomized but realistic intervals.

How it works at a high level:
  1. The engine is started when the Away Mode switch is turned on.
  2. It resolves the configured time window (e.g., "sunset to 23:00") into
     concrete datetime values for today.
  3. If we're currently inside the time window, it immediately begins
     scheduling random entity activations.
  4. If we're before the window, it arms a callback to start at window open.
  5. At window end, all active entities are turned off gracefully.
  6. A daily midnight recalculation handles the fact that sunset/sunrise
     times shift throughout the year.

The simulation produces unpredictable but realistic-looking patterns through
four independent layers of randomness:
  - The gap between consecutive entity activations (random within a range)
  - Which entity gets activated (weighted random, favoring idle entities)
  - How long each entity stays on (random within a range)
  - A cap on simultaneous active entities (prevents all-lights-on scenarios)

No database, no history replay, no network calls — everything is generated
algorithmically from random number generation.

Dependencies:
  - homeassistant.helpers.sun: For resolving "sunset"/"sunrise" to datetimes
  - homeassistant.helpers.event: For scheduling callbacks at specific times
  - homeassistant.core: For calling entity services (turn_on, turn_off)
"""

from __future__ import annotations

import logging
import math
import random
from datetime import date, datetime, timedelta
from typing import Any
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_time,
    async_track_time_change,
)
from homeassistant.helpers.sun import get_astral_event_date, get_astral_event_next
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ENTITIES,
    CONF_INTENSITY,
    CONF_TIME_WINDOW_END,
    CONF_TIME_WINDOW_START,
    DEFAULT_INTENSITY,
    INTENSITY_PROFILES,
)

_LOGGER = logging.getLogger(__name__)


class SimulationEngine:
    """
    The core engine that orchestrates presence simulation.

    This class is responsible for:
      - Resolving the time window configuration into concrete start/end times
      - Scheduling entity on/off events within the active window
      - Choosing which entities to activate using weighted random selection
      - Respecting the maximum simultaneous entity cap from the intensity profile
      - Cleaning up all scheduled callbacks and active entities on stop
      - Recalculating the time window daily (sunset/sunrise shift over the year)

    Lifecycle:
      - Created in __init__.py's async_setup_entry (but NOT started automatically)
      - Started when the AwayModeSwitch is turned on (or restored after restart)
      - Stopped when the switch is turned off or the integration is unloaded

    Thread safety:
      - All methods run on the Home Assistant event loop (single-threaded)
      - No locks are needed because HA's async architecture ensures sequential
        execution of callbacks

    Attributes:
        hass: The Home Assistant instance.
        _entry: The config entry containing user settings.
        _is_running: Whether the simulation is currently active.
        _active_entities: Maps entity_id -> cancel callback for its scheduled
            turn-off event. If an entity is in this dict, it's currently "on"
            as part of the simulation.
        _last_off_time: Maps entity_id -> datetime when it was last turned off.
            Used to weight entity selection toward entities that have been idle
            the longest, creating more realistic movement patterns.
        _scheduled_callbacks: Cancel handles for one-shot window/event timers only
            (not the recurring midnight listener; see _midnight_unsub).
        _midnight_unsub: Unsubscribe for async_track_time_change at 00:00:05; kept
            separate so midnight recalculation does not cancel itself.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """
        Initialize the simulation engine.

        The engine is created in a stopped state. Call start() to begin
        the simulation. This separation allows the switch entity to control
        when simulation actually begins.

        Args:
            hass: The Home Assistant instance, used for service calls,
                  event scheduling, and sun position calculations.
            entry: The config entry containing the user's settings
                   (entities, time window, intensity).
        """
        self.hass = hass
        self._entry = entry

        # Whether the simulation is currently running.
        # This flag is checked by the switch entity to report its state.
        self._is_running: bool = False

        # Currently active (turned on) entities and their turn-off cancel handles.
        # Key: entity_id (str), Value: cancel callback (CALLBACK_TYPE)
        # When an entity is turned on, we schedule a future turn-off and store
        # the cancel handle here. This lets us:
        #   1. Know which entities are currently "on" (for the simultaneous cap)
        #   2. Cancel pending turn-offs if we need to stop abruptly
        self._active_entities: dict[str, CALLBACK_TYPE] = {}

        # Tracks when each entity was last turned off.
        # Key: entity_id (str), Value: datetime when it was turned off
        # This creates a "least recently used" weighting for entity selection:
        # entities that have been idle the longest are more likely to be chosen
        # next, which simulates natural room-to-room movement rather than
        # randomly flickering the same light repeatedly.
        self._last_off_time: dict[str, datetime] = {}

        # One-shot timers (window start/end, next activation). Midnight uses
        # _midnight_unsub so _on_midnight_recalculation can clear this list
        # without unsubscribing the recurring daily listener.
        self._scheduled_callbacks: list[CALLBACK_TYPE] = []
        self._midnight_unsub: CALLBACK_TYPE | None = None

    @property
    def is_running(self) -> bool:
        """
        Whether the simulation engine is currently active.

        Used by the switch entity to report its on/off state.

        Returns:
            True if the simulation is running, False otherwise.
        """
        return self._is_running

    def _get_intensity_profile(self) -> dict[str, Any]:
        """Return the intensity profile dict, falling back if data is invalid."""
        intensity = self._entry.data.get(CONF_INTENSITY, DEFAULT_INTENSITY)
        if intensity not in INTENSITY_PROFILES:
            _LOGGER.warning(
                "Unknown intensity %r, using default %r",
                intensity,
                DEFAULT_INTENSITY,
            )
            intensity = DEFAULT_INTENSITY
        return INTENSITY_PROFILES[intensity]

    # =========================================================================
    # Public API: start() and stop()
    # =========================================================================

    def start(self) -> None:
        """
        Start the simulation engine.

        This is the main entry point called when the Away Mode switch is
        turned on. It:
          1. Sets the running flag
          2. Resolves the time window for today
          3. Determines if we're currently inside the window
          4. Either starts scheduling immediately or arms a callback for later
          5. Schedules the window end callback
          6. Sets up daily midnight recalculation for sunset/sunrise shifts

        If start() is called when already running, it does nothing (idempotent).
        """
        if self._is_running:
            _LOGGER.debug("Simulation already running, ignoring start()")
            return

        _LOGGER.info("Starting Away Mode simulation engine")
        self._is_running = True

        # Resolve the time window for today.
        # This converts "sunset"/"sunrise" keywords into concrete datetimes
        # using the HA instance's configured latitude/longitude.
        self._setup_daily_schedule()

        # Schedule a daily recalculation at midnight.
        # Sunset and sunrise times shift throughout the year, so we need to
        # recalculate the concrete window times each day. We use HA's
        # async_track_time_change to fire a callback at 00:00:05 every day
        # (5 seconds past midnight to avoid any edge cases with day boundaries).
        self._midnight_unsub = async_track_time_change(
            self.hass,
            self._on_midnight_recalculation,
            hour=0,
            minute=0,
            second=5,
        )

    def stop(self) -> None:
        """
        Stop the simulation engine and clean up all state.

        This is called when:
          - The Away Mode switch is turned off
          - The integration is being unloaded (e.g., removed or HA shutting down)

        It performs a complete cleanup:
          1. Cancels the recurring midnight listener (_midnight_unsub), then all
             one-shot timers in _scheduled_callbacks (window start/end, next event),
             then all entity turn-off timers
          2. Turns off all currently active entities so the house doesn't end
             up with lights stuck on after stopping the simulation
          3. Clears all internal state

        If stop() is called when already stopped, it does nothing (idempotent).
        """
        if not self._is_running:
            _LOGGER.debug("Simulation already stopped, ignoring stop()")
            return

        _LOGGER.info("Stopping Away Mode simulation engine")
        self._is_running = False

        # Cancel recurring midnight listener first (not in _scheduled_callbacks).
        if self._midnight_unsub is not None:
            self._midnight_unsub()
            self._midnight_unsub = None

        for cancel_callback in self._scheduled_callbacks:
            cancel_callback()
        self._scheduled_callbacks.clear()

        # Cancel all pending entity turn-off timers and turn off active entities.
        # We need to do this so lights/switches don't stay on forever after
        # the simulation is stopped.
        for entity_id, cancel_turn_off in self._active_entities.items():
            # Cancel the scheduled turn-off (it would fire after we've stopped).
            cancel_turn_off()
            # Turn off the entity now.
            self._turn_off_entity_service(entity_id)

        # Clear the active entities dict now that everything is turned off.
        self._active_entities.clear()

        _LOGGER.info("Away Mode simulation engine stopped, all entities turned off")

    # =========================================================================
    # Daily Schedule Setup
    # =========================================================================

    def _setup_daily_schedule(self) -> None:
        """
        Set up the simulation schedule for today.

        This method is called both on initial start() and at midnight each day
        (for ongoing simulations). It:
          1. Resolves the time window to concrete datetimes for today
          2. Determines the current position relative to the window
          3. Sets up the appropriate callbacks

        There are three possible states:
          a) We're BEFORE the window: Schedule a callback at window start
          b) We're INSIDE the window: Start scheduling events immediately
          c) We're AFTER the window: Schedule for tomorrow's window

        The method also always schedules a callback at window end to turn
        everything off gracefully.
        """
        now = dt_util.now()

        # Resolve "sunset"/"sunrise"/time strings to concrete datetimes.
        window_start = self._resolve_time(
            self._entry.data.get(CONF_TIME_WINDOW_START, "sunset")
        )
        window_end = self._resolve_time(
            self._entry.data.get(CONF_TIME_WINDOW_END, "23:00:00")
        )

        # Handle overnight windows (e.g., "sunset" to "sunrise" or "22:00" to "02:00").
        # If the resolved end time is before the start time, it means the window
        # spans midnight, so we push the end to tomorrow.
        if window_end <= window_start:
            window_end += timedelta(days=1)

        _LOGGER.info(
            "Away Mode time window resolved: %s to %s (now: %s)",
            window_start.strftime("%H:%M:%S"),
            window_end.strftime("%H:%M:%S"),
            now.strftime("%H:%M:%S"),
        )

        if now < window_start:
            # --- State A: Before the window ---
            # The window hasn't opened yet today. Arm a callback to start
            # scheduling when the window opens.
            _LOGGER.info(
                "Currently before window, scheduling start at %s",
                window_start.strftime("%H:%M:%S"),
            )
            cancel_start = async_track_point_in_time(
                self.hass,
                self._on_window_start,
                window_start,
            )
            self._scheduled_callbacks.append(cancel_start)

            # Also schedule the window end callback.
            cancel_end = async_track_point_in_time(
                self.hass,
                self._on_window_end,
                window_end,
            )
            self._scheduled_callbacks.append(cancel_end)

        elif now < window_end:
            # --- State B: Inside the window ---
            # The window is currently open. Start scheduling events immediately.
            _LOGGER.info("Currently inside window, starting simulation immediately")

            # Schedule the window end callback.
            cancel_end = async_track_point_in_time(
                self.hass,
                self._on_window_end,
                window_end,
            )
            self._scheduled_callbacks.append(cancel_end)

            # Begin the event scheduling loop.
            self._schedule_next_event(window_end)

        else:
            # --- State C: After the window ---
            # The window has already passed for today. This can happen if the
            # user turns on the switch late at night after the window closed.
            # We'll just wait for tomorrow's midnight recalculation to set up
            # the next day's schedule.
            _LOGGER.info(
                "Currently after today's window (ended %s), "
                "will start with tomorrow's window at midnight recalc",
                window_end.strftime("%H:%M:%S"),
            )

    # =========================================================================
    # Time Resolution
    # =========================================================================

    def _sun_event_on_date(self, event: str, day: date) -> datetime:
        """Resolve sunset or sunrise on a calendar day, with polar-edge fallback."""
        resolved = get_astral_event_date(self.hass, event, day)
        if resolved is not None:
            return resolved
        return get_astral_event_next(self.hass, event, dt_util.now())

    def _resolve_time(self, time_config: str) -> datetime:
        """
        Convert a time configuration value to a concrete datetime.

        The time configuration can be one of three formats:
          - "sunset": Today's sunset on the local calendar date (see _sun_event_on_date)
          - "sunrise": Today's sunrise on the local calendar date
          - "HH:MM:SS": A fixed time string, combined with today's date

        Sunset/sunrise use get_astral_event_date for the current local day so
        mixed astral + custom bounds (e.g. sunset to 23:00) stay on the same
        evening. Polar edge cases fall back to get_astral_event_next.

        Args:
            time_config: One of "sunset", "sunrise", or a "HH:MM:SS" string.

        Returns:
            A timezone-aware datetime for the resolved instant.
        """
        if time_config == "sunset":
            # Anchor sunset to the current local calendar day so a custom end
            # time on the same evening (e.g. sunset → 23:00) stays consistent.
            # get_astral_event_next(now) alone would jump to tomorrow after
            # sunset, making window_end earlier than window_start.
            local_date = dt_util.as_local(dt_util.now()).date()
            result = self._sun_event_on_date("sunset", local_date)
            _LOGGER.debug("Resolved 'sunset' to %s", result)
            return result

        elif time_config == "sunrise":
            local_date = dt_util.as_local(dt_util.now()).date()
            result = self._sun_event_on_date("sunrise", local_date)
            _LOGGER.debug("Resolved 'sunrise' to %s", result)
            return result

        else:
            # Fixed time string like "23:00:00" or "18:30:00".
            # Parse the time and combine with today's date in the HA timezone.
            try:
                # Split the time string into components.
                parts = time_config.split(":")
                hour = int(parts[0])
                minute = int(parts[1]) if len(parts) > 1 else 0
                second = int(parts[2]) if len(parts) > 2 else 0

                # Get today's date in the HA-configured timezone and combine
                # with the parsed time.
                now = dt_util.now()
                result = now.replace(
                    hour=hour, minute=minute, second=second, microsecond=0
                )
                _LOGGER.debug("Resolved '%s' to %s", time_config, result)
                return result

            except (ValueError, IndexError) as err:
                # If parsing fails, fall back to the current time.
                # This shouldn't happen in normal use since the config flow
                # validates the time format, but we handle it gracefully.
                _LOGGER.error(
                    "Failed to parse time '%s': %s. Using current time as fallback.",
                    time_config,
                    err,
                )
                return dt_util.now()

    # =========================================================================
    # Window Boundary Callbacks
    # =========================================================================

    @callback
    def _on_window_start(self, now: datetime) -> None:
        """
        Callback fired when the simulation time window opens.

        This is triggered by async_track_point_in_time at the resolved
        window start time. It kicks off the event scheduling loop.

        Args:
            now: The current time (provided by HA's event system, may differ
                 slightly from the scheduled time due to event loop delays).
        """
        if not self._is_running:
            # Safety check: the engine may have been stopped between when
            # this callback was scheduled and when it fires.
            return

        _LOGGER.info("Simulation window has opened, beginning entity scheduling")

        # Resolve the end time again to get an accurate value.
        # (Sunset/sunrise times are very slightly different from when we
        # first resolved them, but this ensures maximum accuracy.)
        window_end = self._resolve_time(
            self._entry.data.get(CONF_TIME_WINDOW_END, "23:00:00")
        )

        # Handle overnight: if end is before now, push to tomorrow.
        if window_end <= now:
            window_end += timedelta(days=1)

        # Start the scheduling loop.
        self._schedule_next_event(window_end)

    @callback
    def _on_window_end(self, now: datetime) -> None:
        """
        Callback fired when the simulation time window closes.

        This gracefully ends the current simulation session by:
          1. Cancelling all pending entity turn-off timers
          2. Turning off all currently active entities
          3. Clearing the active entities state

        The engine remains in the "running" state so it will automatically
        start again at the next window start (handled by midnight recalc).

        Args:
            now: The current time (provided by HA's event system).
        """
        if not self._is_running:
            return

        _LOGGER.info("Simulation window has closed, turning off all active entities")

        # Cancel all pending entity turn-off timers.
        for entity_id, cancel_turn_off in self._active_entities.items():
            cancel_turn_off()
            self._turn_off_entity_service(entity_id)

        self._active_entities.clear()

        # Note: We don't cancel the midnight recalculation callback here.
        # The engine is still "running" (the switch is still on), so at
        # midnight, _on_midnight_recalculation will set up tomorrow's schedule.

    @callback
    def _on_midnight_recalculation(self, now: datetime) -> None:
        """
        Callback fired at midnight to recalculate the daily schedule.

        Sunset and sunrise times shift throughout the year (by several minutes
        per day in mid-latitudes, more dramatically near the poles). This
        callback fires at 00:00:05 each day to resolve fresh window times
        and set up the day's schedule.

        This is a lightweight operation — it just resolves two times and
        schedules two callbacks (window start and window end).

        Args:
            now: The current time at midnight (provided by HA's event system).
        """
        if not self._is_running:
            return

        _LOGGER.info(
            "Midnight recalculation: resolving new time window for today"
        )

        # Cancel one-shot window/event timers only. The midnight listener must
        # stay registered (handle lives in _midnight_unsub, not this list).
        for cancel_cb in self._scheduled_callbacks:
            try:
                cancel_cb()
            except Exception:  # noqa: BLE001
                pass
        self._scheduled_callbacks.clear()

        self._setup_daily_schedule()

    # =========================================================================
    # Event Scheduling Loop
    # =========================================================================

    def _schedule_next_event(self, window_end: datetime) -> None:
        """
        Schedule the next entity activation event.

        This is the core scheduling loop. Each call schedules a single future
        event (turning on one entity) after a random delay. When that event
        fires, it in turn calls _schedule_next_event again, creating a
        self-perpetuating chain of events that continues until the window ends.

        The delay between events is randomized within the intensity profile's
        gap_min and gap_max range. This randomization is what makes the
        simulation unpredictable — an observer cannot predict when the next
        light will turn on.

        Args:
            window_end: The datetime when the current simulation window closes.
                        Used to avoid scheduling events that would fire after
                        the window ends.
        """
        if not self._is_running:
            return

        profile = self._get_intensity_profile()

        # Calculate a random delay in minutes, then convert to seconds.
        # The delay is uniformly distributed between gap_min and gap_max.
        # Using uniform (float) rather than randint gives us sub-minute
        # precision, which further reduces predictability.
        delay_minutes = random.uniform(profile["gap_min"], profile["gap_max"])
        delay_seconds = delay_minutes * 60

        # Check if this event would fire after the window closes.
        # If so, don't schedule it — the window end callback will handle cleanup.
        now = dt_util.now()
        event_time = now + timedelta(seconds=delay_seconds)

        if event_time >= window_end:
            _LOGGER.debug(
                "Next event would fire at %s, after window end %s — skipping",
                event_time.strftime("%H:%M:%S"),
                window_end.strftime("%H:%M:%S"),
            )
            return

        _LOGGER.debug(
            "Scheduling next activation event in %.1f minutes (at %s)",
            delay_minutes,
            event_time.strftime("%H:%M:%S"),
        )

        # Schedule the event using HA's async_call_later.
        # This fires _execute_event after delay_seconds on the event loop.
        # We pass window_end through so the recursive call can check bounds.
        @callback
        def _fire_event(_now: datetime) -> None:
            """Inner callback that fires the event and schedules the next one."""
            # Remove our own cancel handle from the list now that we've fired,
            # preventing the _scheduled_callbacks list from growing unboundedly
            # over a multi-day simulation session.
            if cancel in self._scheduled_callbacks:
                self._scheduled_callbacks.remove(cancel)
            self._execute_event(window_end)

        cancel = async_call_later(self.hass, delay_seconds, _fire_event)
        self._scheduled_callbacks.append(cancel)

    @callback
    def _execute_event(self, window_end: datetime) -> None:
        """
        Execute a single simulation event: turn on one entity.

        This method is called when a scheduled activation fires. It:
          1. Chooses an entity to turn on (or skips if at capacity)
          2. Turns the entity on via HA service call
          3. Schedules a random-duration turn-off for that entity
          4. Recursively schedules the next activation event

        The entity choice and timing are randomized to create unpredictable
        but realistic patterns.

        Args:
            window_end: When the current simulation window ends, passed through
                        to _schedule_next_event for bounds checking.
        """
        if not self._is_running:
            return

        # Choose which entity to activate.
        # Returns None if we're at the simultaneous entity cap.
        entity_id = self._choose_entity()

        if entity_id is not None:
            # Turn on the chosen entity.
            _LOGGER.info("Simulation: turning ON %s", entity_id)
            self._turn_on_entity_service(entity_id)

            # Schedule a random-duration turn-off for this entity.
            self._schedule_entity_turn_off(entity_id)
        else:
            _LOGGER.debug(
                "At simultaneous entity cap or no available entities, "
                "skipping this activation cycle"
            )

        # Schedule the next activation event (continues the loop).
        self._schedule_next_event(window_end)

    # =========================================================================
    # Entity Selection
    # =========================================================================

    def _choose_entity(self) -> str | None:
        """
        Choose which entity to activate next using weighted random selection.

        The selection algorithm:
          1. Check if we're at the maximum simultaneous entity cap (from the
             intensity profile). If so, return None to skip this cycle.
          2. Build a list of available entities (configured but not currently on).
          3. Weight each entity by how long it's been idle — entities that were
             turned off longest ago get higher weights. This creates a natural
             "room-to-room movement" pattern instead of randomly flickering
             the same light.
          4. Select one entity using weighted random choice.

        The weighting is important for realism:
          - Without it, you might see: kitchen ON, kitchen OFF, kitchen ON, kitchen OFF
            (the same light flickering, which looks automated)
          - With it, you're more likely to see: kitchen ON, bedroom ON, kitchen OFF,
            living room ON (natural movement through the house)

        Returns:
            The entity_id to activate, or None if at capacity or no entities available.
        """
        # Get all configured entities from the config entry.
        all_entities = self._entry.data.get(CONF_ENTITIES, [])

        if not all_entities:
            _LOGGER.warning("No entities configured for simulation")
            return None

        profile = self._get_intensity_profile()

        # Calculate the maximum number of entities that can be on at once.
        # We use math.ceil to ensure at least 1 entity can be on, even with
        # a small entity list and low max_simultaneous fraction.
        max_on = max(1, math.ceil(len(all_entities) * profile["max_simultaneous"]))

        # Check if we're at capacity.
        if len(self._active_entities) >= max_on:
            _LOGGER.debug(
                "At capacity: %d/%d entities active",
                len(self._active_entities),
                max_on,
            )
            return None

        # Build the list of available entities (not currently active).
        available = [e for e in all_entities if e not in self._active_entities]

        if not available:
            # All entities are currently on (shouldn't happen due to cap check,
            # but handle it gracefully).
            _LOGGER.debug("No available entities (all are currently active)")
            return None

        # Calculate weights for each available entity.
        # Entities that have been idle longer get higher weights.
        now = dt_util.now()
        weights = []

        for entity_id in available:
            last_off = self._last_off_time.get(entity_id)

            if last_off is None:
                # Entity has never been turned off in this session.
                # Give it a high weight to ensure it gets included in the
                # simulation (otherwise new entities would never be chosen).
                weights.append(100.0)
            else:
                # Weight is proportional to idle time in minutes.
                # An entity idle for 30 minutes gets a weight of 30.
                # An entity idle for 5 minutes gets a weight of 5.
                # This naturally favors entities that have been off longer.
                idle_minutes = (now - last_off).total_seconds() / 60.0
                # Minimum weight of 1.0 to ensure every entity has a chance,
                # even if it was just turned off moments ago.
                weights.append(max(1.0, idle_minutes))

        # Weighted random selection.
        # random.choices returns a list; we take the first (and only) element.
        chosen = random.choices(available, weights=weights, k=1)[0]

        _LOGGER.debug(
            "Chose entity %s from %d available (weights: %s)",
            chosen,
            len(available),
            {e: f"{w:.1f}" for e, w in zip(available, weights)},
        )

        return chosen

    # =========================================================================
    # Entity Turn-Off Scheduling
    # =========================================================================

    def _schedule_entity_turn_off(self, entity_id: str) -> None:
        """
        Schedule a future turn-off for a specific entity.

        After turning on an entity, we schedule it to turn off after a random
        duration within the intensity profile's on_min to on_max range. This
        duration varies per activation, so the same light might stay on for
        20 minutes one time and 40 minutes the next.

        The cancel handle is stored in _active_entities so we can:
          - Cancel it if stop() is called before the timer fires
          - Know that this entity is currently "on" (for the simultaneous cap)

        Args:
            entity_id: The entity to schedule a turn-off for.
        """
        profile = self._get_intensity_profile()

        # Random duration in minutes, then convert to seconds.
        # Using uniform (float) gives sub-minute precision for less predictability.
        on_duration_minutes = random.uniform(profile["on_min"], profile["on_max"])
        on_duration_seconds = on_duration_minutes * 60

        _LOGGER.debug(
            "Scheduling turn-off for %s in %.1f minutes",
            entity_id,
            on_duration_minutes,
        )

        # Schedule the turn-off callback.
        @callback
        def _turn_off_callback(_now: datetime) -> None:
            """Turn off the entity and update tracking state."""
            self._handle_entity_turn_off(entity_id)

        cancel = async_call_later(self.hass, on_duration_seconds, _turn_off_callback)

        # Store the cancel handle in the active entities dict.
        # If there's already a cancel handle for this entity (shouldn't happen
        # in normal operation), cancel the old one first.
        if entity_id in self._active_entities:
            self._active_entities[entity_id]()
        self._active_entities[entity_id] = cancel

    def _handle_entity_turn_off(self, entity_id: str) -> None:
        """
        Handle the actual turn-off of an entity when its timer expires.

        This is called by the scheduled turn-off callback. It:
          1. Turns off the entity via HA service call
          2. Removes it from the active entities dict
          3. Records the turn-off time for future selection weighting

        Args:
            entity_id: The entity to turn off.
        """
        if not self._is_running:
            # Safety: engine was stopped between scheduling and firing.
            return

        _LOGGER.info("Simulation: turning OFF %s", entity_id)

        # Turn off the entity.
        self._turn_off_entity_service(entity_id)

        # Remove from active entities tracking.
        self._active_entities.pop(entity_id, None)

        # Record when this entity was turned off.
        # This timestamp is used by _choose_entity to weight selection
        # toward entities that have been idle longer.
        self._last_off_time[entity_id] = dt_util.now()

    # =========================================================================
    # Home Assistant Service Calls
    # =========================================================================

    def _turn_on_entity_service(self, entity_id: str) -> None:
        """
        Turn on an entity via Home Assistant's service call system.

        We determine the correct service domain from the entity_id prefix
        (e.g., "light.living_room" -> domain "light") and call the standard
        "turn_on" service. This works for all supported entity types:
          - light.turn_on
          - switch.turn_on
          - media_player.turn_on
          - fan.turn_on

        Args:
            entity_id: The fully qualified entity ID (e.g., "light.living_room").
        """
        # Extract the domain from the entity_id.
        # "light.living_room" -> "light"
        domain = entity_id.split(".")[0]

        # Fire-and-forget service call. We don't await the result because:
        # 1. Service calls are reliable within HA
        # 2. We don't need to know if the call succeeded (the entity may be
        #    unavailable, and that's fine — we just move on)
        # Note: entity_id goes in service_data, not in a "target" kwarg.
        # The "target" parameter is for automations/scripts; for
        # hass.services.async_call, entity_id belongs in service_data.
        self.hass.async_create_task(
            self.hass.services.async_call(
                domain,
                "turn_on",
                service_data={"entity_id": entity_id},
            )
        )

    def _turn_off_entity_service(self, entity_id: str) -> None:
        """
        Turn off an entity via Home Assistant's service call system.

        Same approach as _turn_on_entity_service but calls "turn_off" instead.

        Args:
            entity_id: The fully qualified entity ID (e.g., "light.living_room").
        """
        # Extract the domain from the entity_id.
        domain = entity_id.split(".")[0]

        # Fire-and-forget service call.
        # entity_id goes in service_data (not "target").
        self.hass.async_create_task(
            self.hass.services.async_call(
                domain,
                "turn_off",
                service_data={"entity_id": entity_id},
            )
        )
