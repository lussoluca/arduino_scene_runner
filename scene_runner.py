"""Run scripted Arduino + audio scenes from a simple YAML timeline.

A scene is a list of timed *cues*. Each cue has an `at` time (seconds from
the start of the scene) and an `action`. Continuous actions (blink, servo
sweep) keep running on a background tick until they finish or are stopped.

Scene format (YAML)
-------------------
    name: My Scene
    end: 15            # optional hard stop (seconds); else runs until
                       # last cue fires and all continuous actions finish
    cues:
      - at: 0    action: blink   pin: 13  interval: 0.5
      - at: 2    action: blink   pin: 12  interval: 0.25
      - at: 4    action: audio   file: music.mp3
      - at: 5    action: servo   pin: 9   angle: 180  duration: 2
      - at: 8    action: on      pin: 11
      - at: 10   action: stop    pin: 13          # stop one pin's behavior
      - at: 12   action: stop_all                 # stop everything

Actions
-------
    on        pin                       digital HIGH
    off       pin                       digital LOW
    blink     pin interval              toggle pin every `interval` seconds
    servo     pin angle [duration]      move servo; sweep over `duration`
                                        seconds if given, else jump
    audio     file [replace: true]     play sound file (non-blocking); with
              [loop: true]              replace, stop current audio first
                                        (change the music); with loop, repeat
                                        the track until stopped
    stop_audio                          stop all audio, leave LEDs/servos
    wait      pin [to: high|low]        freeze the WHOLE scene until the input
                                        pin transitions to `to` (default high);
                                        audio + motion pause and resume together
    stop      pin                       stop blink/sweep on that pin
    stop_all                            stop all behaviors, audio, LEDs off

Triggers (concurrent, non-freezing)
-----------------------------------
A trigger watches a button while the main timeline keeps running. On the
edge it injects its `do` cues (their `at` is an offset from the press), so
the press *changes* what is happening without pausing anything.

    triggers:
      - name: go                # optional label for logs
        pin: 2
        to: high                # edge that fires it (default high)
        once: true              # fire only first press; false = every press
        do:
          - { at: 0,   action: blink, pin: 12, interval: 0.15 }
          - { at: 0,   action: audio, file: track_b.aiff, replace: true }
          - { at: 0.2, action: servo, pin: 9, angle: 180, duration: 1 }

The scene stays alive while any trigger is still armed (a once:false trigger
keeps it alive indefinitely, so add `end:` or a stop_all to finish).

Run:
    python scene_runner.py scene.yaml [PORT]
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

import yaml

from arduino_controller import ArduinoController
from audio_player import AudioPlayer
from config import default_port

TICK = 0.02  # seconds between engine updates


@dataclass
class Blinker:
    pin: int
    interval: float
    state: bool = False
    next_toggle: float = 0.0


@dataclass
class Sweep:
    pin: int
    start_angle: float
    end_angle: float
    start_t: float
    duration: float


@dataclass
class WaitState:
    """An active pause waiting for a digital-input edge."""

    pin: int
    target: bool          # the level that ends the wait (True = HIGH)
    since: float = 0.0    # wall-clock time the pause began
    seen_opposite: bool = False  # require a transition, not a held level


@dataclass
class Trigger:
    """A button-press handler that injects cues while the scene keeps running.

    Unlike `wait`, a trigger does NOT freeze the timeline. On the configured
    edge it schedules its `do` cues (their `at` is an offset from the press).
    """

    pin: int
    target: bool                 # level that fires it (True = HIGH)
    do: list[dict]               # cues to schedule on press
    once: bool = True            # fire only the first press, or re-arm
    name: str = ""
    fired: bool = False
    seen_opposite: bool = False  # require a transition, not a held level

    @property
    def armed(self) -> bool:
        return not (self.once and self.fired)


@dataclass
class SceneRunner:
    board: ArduinoController
    cues: list[dict]
    end: float | None = None
    debug: bool = True
    triggers: list[Trigger] = field(default_factory=list)
    _blinkers: dict[int, Blinker] = field(default_factory=dict)
    _sweeps: dict[int, Sweep] = field(default_factory=dict)
    _servo_angle: dict[int, float] = field(default_factory=dict)
    _players: list[AudioPlayer] = field(default_factory=list)
    _wait: WaitState | None = None
    _pending: list[dict] = field(default_factory=list)  # cues with abs "_t"

    @classmethod
    def from_file(cls, path: str, board: ArduinoController) -> "SceneRunner":
        with open(path) as fh:
            scene = yaml.safe_load(fh)
        cues = sorted(scene.get("cues", []), key=lambda c: c["at"])
        triggers = [
            Trigger(
                pin=t["pin"],
                target=str(t.get("to", "high")).lower() != "low",
                do=sorted(t.get("do", []), key=lambda c: c["at"]),
                once=t.get("once", True),
                name=t.get("name", f"pin{t['pin']}"),
            )
            for t in scene.get("triggers", [])
        ]
        return cls(
            board=board, cues=cues, end=scene.get("end"), triggers=triggers
        )

    # --- debug logging -------------------------------------------------

    def _log(self, now: float, msg: str) -> None:
        if self.debug:
            print(f"[{now:6.2f}s] {msg}", flush=True)

    @staticmethod
    def _fmt_cue(cue: dict) -> str:
        action = cue["action"]
        params = " ".join(
            f"{k}={v}" for k, v in cue.items() if k not in ("at", "action", "_t")
        )
        return f"{action:<9}{params}".rstrip()

    # --- cue dispatch --------------------------------------------------

    def _fire(self, cue: dict, now: float) -> None:
        self._log(now, self._fmt_cue(cue))
        action = cue["action"]
        if action == "on":
            self._stop_pin(cue["pin"])
            self.board.pin_on(cue["pin"])
        elif action == "off":
            self._stop_pin(cue["pin"])
            self.board.pin_off(cue["pin"])
        elif action == "blink":
            self._blinkers[cue["pin"]] = Blinker(
                pin=cue["pin"], interval=cue["interval"], next_toggle=now
            )
        elif action == "servo":
            self._start_servo(cue, now)
        elif action == "audio":
            if cue.get("replace"):  # swap music: stop any current playback
                self._stop_audio()
            player = AudioPlayer(cue["file"], loop=bool(cue.get("loop")))
            player.play()
            self._players.append(player)
        elif action == "stop_audio":
            self._stop_audio()
        elif action == "stop":
            self._stop_pin(cue["pin"])
        elif action == "stop_all":
            self._stop_all()
        elif action == "wait":
            target = str(cue.get("to", "high")).lower() != "low"
            self.board.setup_input(cue["pin"])
            self._wait = WaitState(pin=cue["pin"], target=target)
        else:
            raise ValueError(f"unknown action: {action!r}")

    def _start_servo(self, cue: dict, now: float) -> None:
        pin, angle = cue["pin"], cue["angle"]
        self._sweeps.pop(pin, None)
        duration = cue.get("duration")
        if duration:
            self._sweeps[pin] = Sweep(
                pin=pin,
                start_angle=self._servo_angle.get(pin, 0.0),
                end_angle=angle,
                start_t=now,
                duration=duration,
            )
        else:
            self.board.set_servo(pin, angle)
            self._servo_angle[pin] = angle

    # --- continuous behavior updates -----------------------------------

    def _update_blinkers(self, now: float) -> None:
        for b in self._blinkers.values():
            if now >= b.next_toggle:
                b.state = not b.state
                self.board.set_pin(b.pin, b.state)
                b.next_toggle = now + b.interval

    def _update_sweeps(self, now: float) -> None:
        done = []
        for pin, s in self._sweeps.items():
            frac = (now - s.start_t) / s.duration
            frac = max(0.0, min(1.0, frac))
            angle = s.start_angle + (s.end_angle - s.start_angle) * frac
            self.board.set_servo(pin, angle)
            self._servo_angle[pin] = angle
            if frac >= 1.0:
                done.append(pin)
        for pin in done:
            self._sweeps.pop(pin, None)

    # --- stopping ------------------------------------------------------

    def _stop_pin(self, pin: int) -> None:
        self._blinkers.pop(pin, None)
        self._sweeps.pop(pin, None)

    def _stop_audio(self) -> None:
        for p in self._players:
            p.stop()
        self._players.clear()

    def _stop_all(self) -> None:
        for b in self._blinkers.values():
            self.board.pin_off(b.pin)
        self._blinkers.clear()
        self._sweeps.clear()
        self._stop_audio()

    def _busy(self) -> bool:
        return bool(self._blinkers or self._sweeps) or any(
            p.is_playing() for p in self._players
        )

    # --- input edges ---------------------------------------------------

    @staticmethod
    def _edge(value, target: bool, holder) -> bool:
        """True on a transition to `target`. Requires the pin to first sit at
        the opposite level, so a held button does not fire repeatedly."""
        if value is None:
            return False
        if not holder.seen_opposite:
            if value != target:
                holder.seen_opposite = True
            return False
        return value == target

    def _wait_satisfied(self) -> bool:
        w = self._wait
        assert w is not None
        return self._edge(self.board.read_digital(w.pin), w.target, w)

    def _poll_triggers(self, now: float) -> None:
        """Fire any armed trigger whose pin just hit its edge (non-blocking)."""
        for t in self.triggers:
            if not t.armed:
                continue
            if self._edge(self.board.read_digital(t.pin), t.target, t):
                t.fired = True
                t.seen_opposite = False  # re-arm for the next press
                self._log(now, f"TRIGGER   {t.name} pin={t.pin} +{len(t.do)} cues")
                self._schedule(t.do, now)

    def _schedule(self, cues: list[dict], base_t: float) -> None:
        """Inject cues into the running timeline at base_t + each cue's `at`."""
        for c in cues:
            self._pending.append({**c, "_t": base_t + c["at"]})
        self._pending.sort(key=lambda c: c["_t"])

    def _armed_triggers(self) -> bool:
        return any(t.armed for t in self.triggers)

    def _pause_audio(self) -> None:
        for p in self._players:
            p.pause()

    def _resume_audio(self) -> None:
        for p in self._players:
            p.resume()

    # --- main loop -----------------------------------------------------

    def run(self) -> None:
        n_trig = len(self.triggers)
        self._log(0.0, f"scene start ({len(self.cues)} cues, {n_trig} triggers)")
        for t in self.triggers:
            self.board.setup_input(t.pin)
        self._pending = sorted(
            ({**c, "_t": c["at"]} for c in self.cues), key=lambda c: c["_t"]
        )
        start = time.monotonic()
        paused = 0.0  # total time spent frozen on waits
        while True:
            real = time.monotonic()

            # Frozen on a `wait`: scene clock does not advance.
            if self._wait is not None:
                if self._wait_satisfied():
                    frozen = real - self._wait.since
                    paused += frozen
                    self._log(
                        real - start - paused,
                        f"resume    pin={self._wait.pin} (waited {frozen:.2f}s)",
                    )
                    self._resume_audio()
                    self._wait = None
                else:
                    time.sleep(TICK)
                    continue

            now = real - start - paused

            # Triggers run concurrently: a press injects cues, no freeze.
            self._poll_triggers(now)

            while self._pending and self._pending[0]["_t"] <= now:
                self._fire(self._pending.pop(0), now)
                if self._wait is not None:
                    # A `wait` cue fired: freeze the clock and audio.
                    self._wait.since = time.monotonic()
                    self._pause_audio()
                    self._log(now, f"PAUSED    waiting for pin={self._wait.pin} "
                              f"-> {'HIGH' if self._wait.target else 'LOW'}")
                    break

            self._update_blinkers(now)
            self._update_sweeps(now)

            if self._wait is None:
                if self.end is not None and now >= self.end:
                    break
                # Keep running while triggers may still fire.
                if not self._pending and not self._busy() and not self._armed_triggers():
                    break

            time.sleep(TICK)

        self._stop_all()
        self.board.pin_off(13)
        self._log(time.monotonic() - start - paused, "scene complete")


def main() -> None:
    argv = [a for a in sys.argv[1:] if a != "--quiet"]
    quiet = "--quiet" in sys.argv
    if not argv:
        sys.exit("usage: python scene_runner.py scene.yaml [PORT] [--quiet]")
    scene_path = argv[0]
    port = argv[1] if len(argv) > 1 else default_port()

    with ArduinoController(port) as board:
        runner = SceneRunner.from_file(scene_path, board)
        runner.debug = not quiet
        runner.run()


if __name__ == "__main__":
    main()
