"""Simple Arduino controller built on pyfirmata2.

Requires the Arduino to be flashed with StandardFirmata.

Example:
    from arduino_controller import ArduinoController

    with ArduinoController("/dev/cu.usbmodem213301") as board:
        board.pin_on(13)          # digital write HIGH
        board.pin_off(13)         # digital write LOW
        board.set_servo(9, 90)    # move servo on pin 9 to 90 degrees
"""

from __future__ import annotations

import time

from pyfirmata2 import Arduino, Pin


class ArduinoController:
    """Thin wrapper around a Firmata-enabled Arduino board."""

    def __init__(self, port: str, connect_delay: float = 0.2) -> None:
        """Open a connection to the board.

        Args:
            port: Serial device, e.g. "/dev/cu.usbmodem213301".
            connect_delay: Seconds to wait after opening so Firmata settles.
        """
        self.port = port
        self.board = Arduino(port)
        # Start the background sampler so digital-input callbacks fire.
        self.board.samplingOn()
        time.sleep(connect_delay)
        self._digital: dict[int, Pin] = {}
        self._servo: dict[int, Pin] = {}
        self._input: dict[int, Pin] = {}
        self._input_values: dict[int, bool] = {}

    # --- digital output ------------------------------------------------

    def _digital_pin(self, pin: int) -> Pin:
        """Return (caching) a digital-output pin handle."""
        if pin not in self._digital:
            self._digital[pin] = self.board.get_pin(f"d:{pin}:o")
        return self._digital[pin]

    def pin_on(self, pin: int) -> None:
        """Drive a digital pin HIGH."""
        self._digital_pin(pin).write(1)

    def pin_off(self, pin: int) -> None:
        """Drive a digital pin LOW."""
        self._digital_pin(pin).write(0)

    def set_pin(self, pin: int, state: bool) -> None:
        """Set a digital pin to a boolean state."""
        self._digital_pin(pin).write(1 if state else 0)

    # --- digital input -------------------------------------------------

    def setup_input(self, pin: int) -> Pin:
        """Configure (caching) a digital-input pin and start reporting.

        pyfirmata2 is callback-driven, so the latest value is cached as it
        arrives and exposed via read_digital().
        """
        if pin not in self._input:
            p = self.board.get_pin(f"d:{pin}:i")
            p.register_callback(lambda value, _pin=pin: self._on_input(_pin, value))
            p.enable_reporting()
            self._input[pin] = p
        return self._input[pin]

    def _on_input(self, pin: int, value) -> None:
        """Callback storing the latest reported value for a pin."""
        self._input_values[pin] = bool(value)

    def read_digital(self, pin: int) -> bool | None:
        """Return the latest reported value, or None until the first report."""
        self.setup_input(pin)
        return self._input_values.get(pin)

    # --- servo ---------------------------------------------------------

    def _servo_pin(self, pin: int) -> Pin:
        """Return (caching) a servo pin handle."""
        if pin not in self._servo:
            self._servo[pin] = self.board.get_pin(f"d:{pin}:s")
        return self._servo[pin]

    def set_servo(self, pin: int, angle: float) -> None:
        """Move a servo on the given pin to an angle in degrees (0-180).

        Raises:
            ValueError: If angle is outside 0-180.
        """
        if not 0 <= angle <= 180:
            raise ValueError(f"angle must be 0-180, got {angle}")
        self._servo_pin(pin).write(angle)

    # --- lifecycle -----------------------------------------------------

    def close(self) -> None:
        """Release the serial connection."""
        self.board.exit()

    def __enter__(self) -> "ArduinoController":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
