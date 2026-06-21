"""Shared configuration loading.

Resolves the Arduino serial port from, in order of precedence:
    1. ARDUINO_PORT environment variable
    2. config.yaml next to this file
    3. a built-in fallback
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).with_name("config.yaml")
_FALLBACK_PORT = "/dev/cu.usbmodem213301"


def load_config() -> dict:
    """Read config.yaml, returning an empty dict if it is missing."""
    if _CONFIG_PATH.exists():
        return yaml.safe_load(_CONFIG_PATH.read_text()) or {}
    return {}


def default_port() -> str:
    """Resolve the serial port from env, then config file, then fallback."""
    env = os.environ.get("ARDUINO_PORT")
    if env:
        return env
    return load_config().get("port", _FALLBACK_PORT)
