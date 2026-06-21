"""Example usage of ArduinoController.

Run with the Arduino (flashed with StandardFirmata) connected:

    python example.py [PORT]

Defaults to /dev/cu.usbmodem213301 if no port is given.
"""

import sys
import time

from arduino_controller import ArduinoController
from config import default_port

PORT = sys.argv[1] if len(sys.argv) > 1 else default_port()

LED_PIN = 13
SERVO_PIN = 9


def main() -> None:
    with ArduinoController(PORT) as board:
        # Blink the LED on pin 13 three times.
        for _ in range(3):
            board.pin_on(LED_PIN)
            time.sleep(0.5)
            board.pin_off(LED_PIN)
            time.sleep(0.5)

        # Sweep the servo on pin 9 across its range.
        for angle in (0, 45, 90, 135, 180, 90):
            board.set_servo(SERVO_PIN, angle)
            print(f"servo -> {angle}deg")
            time.sleep(0.6)

    print("done")


if __name__ == "__main__":
    main()
