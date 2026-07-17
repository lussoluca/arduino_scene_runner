"""Read tag UIDs from a dedicated ID-12 reader board over serial.

A second Arduino runs the ``rfid_reader/rfid_reader.ino`` sketch: it reads an
Innovations ID-12 module (125 kHz EM4100 tags) and prints one line per tag
placement:

    UID:4500A2B3C4

This module reads that stream on a background thread and exposes the UID of
the card currently on the reader, or None once it is removed. A card is
considered gone when no line has arrived for ``absent_timeout`` seconds; the
sketch reports each placement once, so this also clears state between reads.

Example:
    from rfid_reader import RfidReader

    with RfidReader("/dev/cu.usbmodem214101") as reader:
        uid = reader.current_uid()   # "4500A2B3C4" or None
"""

from __future__ import annotations

import threading
import time

import serial


def normalize_uid(uid: str) -> str:
    """Return uppercase hex with spaces, colons, dashes and 0x removed.

    Lets a scene write a UID as ``DE:AD:BE:EF``, ``de ad be ef`` or
    ``0xDEADBEEF`` and still match the reader's ``DEADBEEF``.
    """
    u = uid.strip().upper()
    if u.startswith("0X"):
        u = u[2:]
    for ch in " :-":
        u = u.replace(ch, "")
    return u


class RfidReader:
    """Background serial reader for the ID-12 reporter sketch."""

    def __init__(
        self,
        port: str,
        baud: int = 115200,
        absent_timeout: float = 0.5,
        connect_delay: float = 2.0,
    ) -> None:
        """Open the reader board's serial port and start reading.

        Args:
            port: Serial device of the reader board.
            baud: Must match the sketch's Serial.begin (115200).
            absent_timeout: Seconds without a UID line before the card is
                treated as removed.
            connect_delay: Seconds to wait after opening; the Uno resets when
                the port opens and needs time to boot the sketch.
        """
        self.port = port
        self.absent_timeout = absent_timeout
        self._serial = serial.Serial(port, baud, timeout=0.1)
        time.sleep(connect_delay)
        self._serial.reset_input_buffer()
        self._uid: str | None = None
        self._last_seen = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                line = self._serial.readline().decode("ascii", "ignore").strip()
            except (serial.SerialException, OSError):
                break
            if line.startswith("UID:"):
                uid = normalize_uid(line[4:])
                if uid:
                    with self._lock:
                        self._uid = uid
                        self._last_seen = time.monotonic()

    def current_uid(self) -> str | None:
        """Return the UID on the reader now, or None if it is empty."""
        with self._lock:
            if self._uid is None:
                return None
            if time.monotonic() - self._last_seen > self.absent_timeout:
                self._uid = None
            return self._uid

    def close(self) -> None:
        """Stop the reader thread and release the serial connection."""
        self._stop.set()
        self._thread.join(timeout=1.0)
        try:
            self._serial.close()
        except Exception:
            pass

    def __enter__(self) -> "RfidReader":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
