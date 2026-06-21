"""Play audio while driving the Arduino concurrently.

The audio plays in the background; the main loop sweeps a servo and
blinks an LED until the sound finishes.

    python example_audio_sync.py SOUND_FILE [PORT]
"""

import sys
import time

from arduino_controller import ArduinoController
from audio_player import AudioPlayer
from config import default_port

SOUND = sys.argv[1] if len(sys.argv) > 1 else "/System/Library/Sounds/Submarine.aiff"
PORT = sys.argv[2] if len(sys.argv) > 2 else default_port()

LED_PIN = 13
SERVO_PIN = 9


def main() -> None:
    player = AudioPlayer(SOUND)

    with ArduinoController(PORT) as board:
        player.play()
        angle = 0
        step = 30
        led = False

        # Run the Arduino while the sound plays.
        while player.is_playing():
            led = not led
            board.set_pin(LED_PIN, led)

            board.set_servo(SERVO_PIN, angle)
            angle += step
            if angle >= 180 or angle <= 0:
                step = -step
            angle = max(0, min(180, angle))

            time.sleep(0.2)

        board.pin_off(LED_PIN)

    print("audio + motion done")


if __name__ == "__main__":
    main()
