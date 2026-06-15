# Away Mode

A Home Assistant custom integration that simulates presence by randomly turning your lights, switches, fans, and media players on and off while you're away. Makes your home look occupied with minimal setup.

## Why?

Existing solutions either require replaying database history (complex, fragile) or manually building automations with random delays (tedious, hard to maintain). Away Mode gives you a simple switch: turn it on, and your selected entities cycle on and off in realistic, unpredictable patterns — no database, no history, no YAML.

## Features

- **Simple setup** — Pick your entities, set a time window, choose an intensity level. That's it.
- **Sunset/sunrise aware** — Time windows can start or end at sunset or sunrise, automatically adjusting to your location and the season.
- **Overnight windows:** Windows that cross midnight (for example sunset to sunrise, or 22:00 to 02:00) keep simulating through the early-morning hours.
- **Realistic patterns** — Randomized timing gaps, weighted entity selection, varied on-durations, and a simultaneous entity cap prevent predictable sequences.
- **Weighted selection** — Entities that have been off the longest are more likely to turn on next, simulating natural room-to-room movement.
- **Three intensity levels** — Low (quiet house), Medium (typical evening), High (busy household).
- **Survives restarts** — If Home Assistant restarts while Away Mode is on, the simulation automatically resumes.
- **No database dependency** — Patterns are generated algorithmically. No history or recorder integration required.
- **Multiple instances:** Run several Away Modes (for example Downstairs and Upstairs), each with its own name, entities, schedule, and intensity. Each appears as its own device.
- **Switch control:** Toggle the Away Mode switch from the UI, automations, scripts, scenes, or voice assistants. The default entity id is `switch.away_mode`.

## Installation

### HACS (Recommended)

1. Open **HACS** in your Home Assistant instance
2. Click the three-dot menu (top right) and select **Custom repositories**
3. Paste `https://github.com/simplytoast1/ha-away-mode` and select **Integration** as the category, then click **Add**
4. Close the custom repositories dialog
5. Search for **Away Mode** in HACS and click it
6. Click **Download** and restart Home Assistant

### Manual

1. Copy the `custom_components/away_mode` folder into your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings** > **Devices & Services**
2. Click **Add Integration** (bottom right)
3. Search for **Away Mode** and select it
4. You'll see a single setup form with:
   - **Name:** A name for this instance (defaults to "Away Mode"). Used as the device and switch name, so you can tell multiple instances apart.
   - **Entities to simulate** — Pick the lights, switches, fans, and media players you want included
   - **Simulation starts at** — Choose Sunset, Sunrise, or Custom time
   - **Simulation ends at** — Choose Sunset, Sunrise, or Custom time
   - **Activity intensity** — Low, Medium, or High (descriptions are shown in the dropdown)
5. If you chose "Custom time" for start or end, a second screen will ask you to pick the exact time
6. Click **Submit** — done!

### Intensity Levels Explained

| Level | Max entities on at once | Each entity stays on | Gap between new activations |
|-------|------------------------|---------------------|-----------------------------|
| **Low** | 20% of your entities | 20 – 60 min | 10 – 30 min |
| **Medium** | 35% of your entities | 15 – 45 min | 5 – 20 min |
| **High** | 50% of your entities | 10 – 35 min | 3 – 15 min |

### Changing Settings Later

Go to **Settings** > **Devices & Services**, find the **Away Mode** entry, and click **Configure**. Changes apply immediately without restarting the simulation: only entities you removed are turned off, so any unchanged lights that are currently on stay on.

### Multiple Instances

You can add Away Mode more than once. For example, run a "Downstairs" instance on one schedule and an "Upstairs" instance on another. Repeat the configuration steps above and give each a distinct name. Each instance gets its own device and switch named after it (for example `switch.downstairs`, `switch.upstairs`), with its own entities, time window, and intensity.

## Usage

### Basic

Toggle `switch.away_mode` in the Home Assistant UI. When turned on, the simulation runs automatically during the configured time window each day. When turned off, all currently simulated entities are turned off immediately.

### With Automations

Automatically enable Away Mode when everyone leaves, and disable it when someone arrives:

```yaml
automation:
  - alias: "Enable Away Mode when nobody is home"
    trigger:
      - platform: state
        entity_id: group.family
        to: "not_home"
    action:
      - action: switch.turn_on
        target:
          entity_id: switch.away_mode

  - alias: "Disable Away Mode when someone arrives"
    trigger:
      - platform: state
        entity_id: group.family
        to: "home"
    action:
      - action: switch.turn_off
        target:
          entity_id: switch.away_mode
```

> **Tip:** Replace `group.family` with your own person group or zone trigger.

## How It Works

Once the switch is on and the current time is inside the configured window:

1. The engine waits a random amount of time (based on intensity level)
2. It picks an entity to turn on — weighted toward whichever entity has been off the longest
3. It turns that entity on and schedules a random-duration turn-off (also based on intensity)
4. Repeats from step 1

A cap on simultaneous active entities prevents the unrealistic scenario of every light being on at once.

When the time window ends, all active entities are turned off. The next day, the cycle starts again automatically at the configured start time. Sunset and sunrise times are recalculated daily at midnight using your Home Assistant location settings.

### Example Timeline

Medium intensity, 6 entities, sunset to 11 PM:

```
19:12  light.living_room ON
19:27  light.kitchen ON
19:43  light.living_room OFF  (was on 31 min)
19:51  switch.bedroom_lamp ON
20:02  light.kitchen OFF      (was on 35 min)
20:15  light.hallway ON
20:19  switch.bedroom_lamp OFF (was on 28 min)
20:33  light.living_room ON   (idle longest, chosen again)
  ...
23:00  Window closes — all active entities turned off
```

Every night will look different. No two days produce the same pattern.

## Supported Entity Types

| Domain | Example |
|--------|---------|
| Lights | `light.living_room`, `light.kitchen` |
| Switches | `switch.bedroom_lamp`, `switch.porch_light` |
| Media players | `media_player.living_room_tv` |
| Fans | `fan.bedroom_fan` |

## Good to Know

- Away Mode is a **supplement** to physical security (locks, cameras, alarms) — not a replacement.
- The simulation **only runs during the configured time window**. Outside the window, no entities are changed.
- When you **turn the switch off**, all currently simulated entities are turned off immediately.
- If you **turn the switch on outside the time window**, it will wait and automatically start when the window opens.
- Time windows can **cross midnight** (for example sunset to sunrise, or 22:00 to 02:00); the simulation keeps running through the early-morning hours.
- Sunset/sunrise times are **recalculated daily** at midnight, so seasonal changes are handled automatically.
- You can include the `switch.away_mode` entity on your dashboard for quick toggling.

## License

MIT
