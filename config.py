"""Shared configuration loading.

Resolves the Arduino serial port from, in order of precedence:
    1. ARDUINO_PORT environment variable
    2. config.yaml next to this file (unless set to "auto")
    3. serial-port autodetection
    4. a built-in fallback
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from serial.tools import list_ports

_CONFIG_PATH = Path(__file__).with_name("config.yaml")
_FALLBACK_PORT = "/dev/cu.usbmodem213301"

# USB vendor IDs for Arduino and common clones (CH340, CP210x, FTDI).
_ARDUINO_VIDS = {0x2341, 0x2A03, 0x1B4F, 0x239A, 0x1A86, 0x10C4, 0x0403}


def load_config() -> dict:
    """Read config.yaml, returning an empty dict if it is missing."""
    if _CONFIG_PATH.exists():
        return yaml.safe_load(_CONFIG_PATH.read_text()) or {}
    return {}


def detect_port(exclude: str | None = None) -> str | None:
    """Scan serial ports and return the first Arduino-like device, or None.

    Pass ``exclude`` to skip an already-claimed device (e.g. the Firmata board)
    so a second Arduino, such as the RFID reader, resolves to a different port.
    """
    candidates = [p for p in list_ports.comports() if p.device != exclude]
    # Prefer devices whose USB vendor ID is a known Arduino/clone chip.
    for p in candidates:
        if p.vid in _ARDUINO_VIDS:
            return p.device
    # Fall back to matching the device name (macOS usbmodem/usbserial, Linux ttyACM/ttyUSB).
    for p in candidates:
        name = p.device.lower()
        if any(tag in name for tag in ("usbmodem", "usbserial", "ttyacm", "ttyusb")):
            return p.device
    return None


def default_port() -> str:
    """Resolve the serial port from env, then config file, then autodetect, then fallback."""
    env = os.environ.get("ARDUINO_PORT")
    if env:
        return env
    configured = load_config().get("port")
    if configured and configured != "auto":
        return configured
    return detect_port() or _FALLBACK_PORT


def default_rfid_port(exclude: str | None = None) -> str:
    """Resolve the RFID reader board's port from env, config, then autodetect.

    Precedence: RFID_PORT env var, ``rfid_port`` in config.yaml (unless
    "auto"), then the first Arduino-like device other than ``exclude`` (the
    Firmata board). Raises if none can be found, since there is no sensible
    fallback for a second board.
    """
    env = os.environ.get("RFID_PORT")
    if env:
        return env
    configured = load_config().get("rfid_port")
    if configured and configured != "auto":
        return configured
    port = detect_port(exclude=exclude)
    if not port:
        raise RuntimeError(
            "Could not find the RFID reader serial port. Set RFID_PORT or "
            "rfid_port in config.yaml to the reader board's device."
        )
    return port
