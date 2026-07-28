"""Keyboard-driven stand-ins for the Arduino boards (--simulate mode).

Lets a scene run with no hardware attached: digital outputs and servos are
printed to the terminal instead of driving pins, and digital inputs / RFID
tags are driven from the computer keyboard. Audio and video play normally,
since they already run on the computer.

Key bindings are derived from the scene: one key per input pin (from `wait`
cues and button triggers) and one per tag UID (from `uid` triggers). The
mapping is printed at startup. Pressing a pin key pulses the pin to its
active level for a moment; pressing a tag key places the tag on the
simulated reader for a moment.

Example:
    python scene_runner.py scene.yaml --simulate
"""

from __future__ import annotations

import select
import sys
import termios
import threading
import time
import tty
from dataclasses import dataclass

PULSE_SECONDS = 0.4  # how long a simulated button press holds its level
TAG_SECONDS = 1.0    # how long a simulated tag stays on the reader

# Keys handed out to bindings, in order. 'q' is skipped to avoid the
# muscle-memory quit key firing a cue.
KEY_POOL = "123456789abcdefghijklmnoprstuvwxyz"


class SimulatedBoard:
    """Drop-in for ArduinoController that prints instead of driving pins.

    Digital inputs hold a level set by the keyboard handler (via
    ``set_input``); outputs and servo moves are logged, deduplicated so
    blinks stay readable and servo sweeps do not flood the terminal.
    """

    def __init__(self) -> None:
        self._out: dict[int, bool] = {}
        self._inputs: dict[int, bool] = {}
        self._servo_logged: dict[int, float] = {}

    @staticmethod
    def _log(msg: str) -> None:
        print(f"[sim] {msg}", flush=True)

    # --- digital output ------------------------------------------------

    def pin_on(self, pin: int) -> None:
        self.set_pin(pin, True)

    def pin_off(self, pin: int) -> None:
        self.set_pin(pin, False)

    def set_pin(self, pin: int, state: bool) -> None:
        state = bool(state)
        if self._out.get(pin) == state:
            return
        self._out[pin] = state
        self._log(f"pin {pin} {'HIGH' if state else 'LOW'}")

    # --- digital input ---------------------------------------------------

    def setup_input(self, pin: int) -> None:
        self._inputs.setdefault(pin, False)

    def set_input(self, pin: int, state: bool) -> None:
        """Set a simulated input level (called by the keyboard handler)."""
        self._inputs[pin] = bool(state)

    def read_digital(self, pin: int) -> bool | None:
        self.setup_input(pin)
        return self._inputs[pin]

    # --- servo -----------------------------------------------------------

    def set_servo(self, pin: int, angle: float) -> None:
        if not 0 <= angle <= 180:
            raise ValueError(f"angle must be 0-180, got {angle}")
        # Sweeps write every tick; log only ~10-degree steps to stay readable.
        last = self._servo_logged.get(pin)
        if last is not None and abs(angle - last) < 10:
            return
        self._servo_logged[pin] = angle
        self._log(f"servo {pin} -> {angle:.0f}°")

    # --- lifecycle -------------------------------------------------------

    def close(self) -> None:
        pass

    def __enter__(self) -> "SimulatedBoard":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class SimulatedRfidReader:
    """Drop-in for RfidReader; tags are placed by the keyboard handler."""

    def __init__(self, present_seconds: float = TAG_SECONDS) -> None:
        self.present_seconds = present_seconds
        self._uid: str | None = None
        self._until = 0.0
        self._lock = threading.Lock()

    def place_tag(self, uid: str) -> None:
        """Put a tag on the reader for ``present_seconds``."""
        with self._lock:
            self._uid = uid
            self._until = time.monotonic() + self.present_seconds

    def current_uid(self) -> str | None:
        with self._lock:
            if self._uid is not None and time.monotonic() > self._until:
                self._uid = None
            return self._uid

    def close(self) -> None:
        pass


@dataclass
class _PinBinding:
    pin: int
    level: bool  # active level the key pulses the pin to
    label: str


@dataclass
class _TagBinding:
    uid: str
    label: str


class KeyboardSim:
    """Reads single keypresses and drives the simulated inputs/reader.

    Puts the terminal in cbreak mode on ``start`` and restores it on
    ``stop`` (also usable as a context manager). Ctrl-C still works.
    """

    def __init__(
        self, board: SimulatedBoard, reader: SimulatedRfidReader | None = None
    ) -> None:
        self.board = board
        self.reader = reader
        self._bindings: dict[str, _PinBinding | _TagBinding] = {}
        self._keys = iter(KEY_POOL)
        self._timers: dict[int, threading.Timer] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._saved_termios = None

    # --- binding setup ---------------------------------------------------

    def bind_pin(self, pin: int, level: bool, label: str) -> None:
        """Assign the next free key to pulse ``pin`` to ``level``."""
        if any(
            isinstance(b, _PinBinding) and b.pin == pin and b.level == level
            for b in self._bindings.values()
        ):
            return
        # Idle at the opposite level so the pulse is a real edge.
        self.board.setup_input(pin)
        self.board.set_input(pin, not level)
        self._bindings[next(self._keys)] = _PinBinding(pin, level, label)

    def bind_tag(self, uid: str, label: str) -> None:
        """Assign the next free key to place tag ``uid`` on the reader."""
        if any(
            isinstance(b, _TagBinding) and b.uid == uid
            for b in self._bindings.values()
        ):
            return
        self._bindings[next(self._keys)] = _TagBinding(uid, label)

    def bind_scene(self, runner) -> None:
        """Derive bindings from a SceneRunner: wait pins, triggers, tags."""
        for trig in runner.triggers:
            if trig.uid is not None:
                self.bind_tag(trig.uid, trig.name)
            else:
                self.bind_pin(trig.pin, trig.target, trig.name)
        cue_lists = [runner.cues] + [t.do for t in runner.triggers]
        for cues in cue_lists:
            for cue in cues:
                if cue.get("action") == "wait":
                    level = str(cue.get("to", "high")).lower() != "low"
                    self.bind_pin(cue["pin"], level, f"wait pin {cue['pin']}")

    # --- key handling ------------------------------------------------------

    def _press(self, key: str) -> None:
        binding = self._bindings.get(key)
        if binding is None:
            return
        if isinstance(binding, _TagBinding):
            assert self.reader is not None
            print(f"[sim] key '{key}': tag {binding.uid} on reader", flush=True)
            self.reader.place_tag(binding.uid)
            return
        pin, level = binding.pin, binding.level
        print(
            f"[sim] key '{key}': pin {pin} pulse "
            f"{'HIGH' if level else 'LOW'} ({PULSE_SECONDS}s)",
            flush=True,
        )
        old = self._timers.pop(pin, None)
        if old is not None:
            old.cancel()
        self.board.set_input(pin, level)
        timer = threading.Timer(
            PULSE_SECONDS, self.board.set_input, args=(pin, not level)
        )
        timer.daemon = True
        timer.start()
        self._timers[pin] = timer

    def print_mapping(self) -> None:
        print("[sim] simulator mode: no hardware, keyboard drives inputs")
        if not self._bindings:
            print("[sim] scene has no inputs; outputs are printed as they fire")
            return
        print("[sim] keys:")
        for key, b in self._bindings.items():
            if isinstance(b, _TagBinding):
                print(f"[sim]   {key} -> tag {b.uid} ({b.label})")
            else:
                lvl = "HIGH" if b.level else "LOW"
                print(f"[sim]   {key} -> pin {b.pin} {lvl} ({b.label})")
        print("[sim] Ctrl-C stops the scene")

    # --- input loop ----------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            ready, _, _ = select.select([sys.stdin], [], [], 0.1)
            if ready:
                self._press(sys.stdin.read(1))

    def start(self) -> None:
        self.print_mapping()
        if not sys.stdin.isatty():
            print("[sim] stdin is not a terminal; keyboard input disabled")
            return
        fd = sys.stdin.fileno()
        self._saved_termios = termios.tcgetattr(fd)
        tty.setcbreak(fd)  # unbuffered keys; Ctrl-C keeps working
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        for timer in self._timers.values():
            timer.cancel()
        if self._saved_termios is not None:
            termios.tcsetattr(
                sys.stdin.fileno(), termios.TCSADRAIN, self._saved_termios
            )
            self._saved_termios = None

    def __enter__(self) -> "KeyboardSim":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
