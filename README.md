# Arduino Scene Runner

Drive LEDs, servos, and audio from a simple YAML timeline. Define a _scene_ —
a list of timed cues — and the runner plays it against an Arduino (running
StandardFirmata) while sound plays on your computer. Button presses can pause
the scene or change what it is doing, live.

## Requirements

- An Arduino flashed with **StandardFirmata** (Arduino IDE → File → Examples →
  Firmata → StandardFirmata → upload).
- Python 3.10+ with the dependencies installed:

  ```bash
  python -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  ```

- The serial port of your board. Set it once in `config.yaml`:

  ```yaml
  port: /dev/cu.usbmodem213301
  ```

  Override per-run with the `ARDUINO_PORT` environment variable or by passing
  the port as the last CLI argument.

## Running a scene

```bash
python scene_runner.py scene.yaml            # uses port from config.yaml
python scene_runner.py scene.yaml /dev/cu.usbmodem213301
python scene_runner.py scene.yaml --quiet    # silence the cue log
```

While it runs, each fired cue is logged with its scene-time:

```
[  0.00s] scene start (2 cues, 1 triggers)
[  0.00s] blink    pin=13 interval=0.5
[  4.00s] audio    file=music.wav
[  6.12s] TRIGGER  escalate pin=2 +3 cues
```

## Writing a scene

A scene is a YAML file with four top-level keys, all optional except `cues`:

```yaml
name: My Scene # label (cosmetic)
end: 30 # optional hard stop, in seconds
cues: [...] # the timeline
triggers: [...] # button handlers (see below)
```

### Cues

Each cue has an `at` time (seconds from the start of the scene) and an
`action`. Cues can share the same `at`; they fire in the order written.

```yaml
cues:
  - { at: 0, action: blink, pin: 13, interval: 0.5 }
  - { at: 2, action: on, pin: 12 }
  - { at: 4, action: audio, file: music.wav }
  - { at: 5, action: servo, pin: 9, angle: 180, duration: 2 }
  - { at: 10, action: stop_all }
```

### Actions

| Action       | Parameters                             | Effect                                                                                                                                                        |
| ------------ | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `on`         | `pin`                                  | Drive a digital pin HIGH.                                                                                                                                     |
| `off`        | `pin`                                  | Drive a digital pin LOW.                                                                                                                                      |
| `blink`      | `pin`, `interval`                      | Toggle the pin every `interval` seconds. Runs until stopped.                                                                                                  |
| `servo`      | `pin`, `angle`, `duration?`            | Move a servo to `angle` (0–180). With `duration`, sweep over that many seconds; without it, jump instantly.                                                   |
| `audio`      | `file`, `loop?`, `replace?`, `volume?` | Play a sound (non-blocking). `loop: true` repeats it gaplessly. `replace: true` stops any current audio first (use to change the music). `volume` is 0.0–1.0. |
| `stop_audio` | —                                      | Stop all audio; leave LEDs and servos as they are.                                                                                                            |
| `stop`       | `pin`                                  | Stop a `blink` or `servo` sweep on that pin.                                                                                                                  |
| `stop_all`   | —                                      | Stop everything: behaviors cleared, audio stopped, LEDs off.                                                                                                  |
| `wait`       | `pin`, `to?`                           | Freeze the **whole** scene until a button edge (see below).                                                                                                   |

`blink` and `servo` (with `duration`) are _continuous_: they keep running on a
background tick after the cue fires. Everything else is instantaneous.

Audio formats: WAV, MP3, FLAC, OGG, AIFF. WAV has the lowest start latency.

### When does a scene end?

The runner stops when **all** of these are true: no more cues are pending, no
continuous behavior is active, and no trigger is still armed. A looping audio
track or a `once: false` trigger keeps the scene alive forever — add an `end:`
time or a `stop_all` cue to finish, or press Ctrl-C.

## Buttons: two ways to react

### `wait` — freeze until pressed

A `wait` cue stops the entire timeline. Blinks, sweeps, and audio all pause and
resume together. Use it for "hold here until I'm ready".

```yaml
cues:
  - { at: 0, action: blink, pin: 13, interval: 0.3 } # "ready" indicator
  - { at: 1, action: wait, pin: 2, to: high } # freeze until press
  - { at: 1, action: stop, pin: 13 }
  - { at: 1, action: audio, file: music.wav }
  - { at: 1, action: servo, pin: 9, angle: 180, duration: 2 }
```

`to: high` (default) suits a button wired so a press reads HIGH; use `to: low`
for the opposite wiring.

### `triggers` — change the show, keep running

A trigger watches a button **while the main timeline keeps playing**. On the
press it injects its `do` cues, whose `at` is an offset from the moment of the
press. Use it for "press to escalate / branch / change the music".

```yaml
cues:
  - { at: 0, action: blink, pin: 13, interval: 0.5 } # base loop
  - { at: 0, action: audio, file: ambient.wav, loop: true }

triggers:
  - name: escalate # optional label, shown in the log
    pin: 2
    to: high # edge that fires it (default high)
    once: true # fire only the first press; false = every press
    do:
      - { at: 0, action: blink, pin: 12, interval: 0.15 }
      - { at: 0, action: audio, file: drop.wav, replace: true }
      - { at: 0.2, action: servo, pin: 9, angle: 180, duration: 1 }
```

Here the pin-13 blink never stops; pressing the button speeds up a second LED,
swaps the music, and sweeps the servo. Define several triggers on different
pins to branch the scene different ways.

Edge detection requires a real transition: if a button is already held at the
target level when the wait/trigger arms, it must be released and pressed again —
a stuck button will not fire repeatedly.

## Wiring a button

See the wiring notes for a 4-pin tactile button (pull-down, idle LOW / press
HIGH, matching `to: high`):

- `5V` → one pin on the **left** side of the button
- a pin on the **right** side → digital pin (e.g. `D2`)
- same right-side pin → 10 kΩ resistor → `GND`

The two pins on the same side of a 4-pin tactile button are joined internally;
wire one pin from each side (diagonal).

## Library pieces

If you want to script directly in Python instead of YAML:

```python
from arduino_controller import ArduinoController

with ArduinoController("/dev/cu.usbmodem213301") as board:
    board.pin_on(13)
    board.set_servo(9, 90)
    pressed = board.read_digital(2)
```

- `arduino_controller.py` — `ArduinoController`: `pin_on/off`, `set_pin`,
  `set_servo`, `setup_input`, `read_digital`.
- `audio_player.py` — `AudioPlayer`: `play`, `is_playing`, `pause`, `resume`,
  `set_volume`, `stop`, plus `loop=True`.
- `scene_runner.py` — `SceneRunner` and the YAML engine.
- `config.py` — resolves the serial port.

## Example scenes

| File                     | Shows                                             |
| ------------------------ | ------------------------------------------------- |
| `scene.yaml`             | A basic timeline: LEDs, music, servo sweep, stop. |
| `scene_button.yaml`      | `wait` — freeze until a button press.             |
| `scene_interactive.yaml` | `triggers` — press to change the running scene.   |
