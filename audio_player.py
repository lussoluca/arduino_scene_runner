"""Non-blocking audio playback using just_playback (miniaudio).

Each player owns one decoded stream with its own playback thread, so the
main thread stays free to drive the Arduino. Looping is gapless (the
decoder restarts the stream internally — no process respawn).

Supported formats: WAV, MP3, FLAC, OGG/Vorbis.

Example:
    from audio_player import AudioPlayer

    player = AudioPlayer("sound.wav", loop=True)
    player.play()
    while player.is_playing():
        ...   # drive the Arduino here
    player.stop()
"""

from __future__ import annotations

import time

from just_playback import Playback


class AudioPlayer:
    """Gapless, controllable audio playback for one file."""

    def __init__(self, path: str, loop: bool = False, volume: float = 1.0) -> None:
        self.path = path
        self.loop = loop
        self._pb = Playback()
        self._pb.load_file(path)
        self._pb.loop_at_end(loop)
        self._pb.set_volume(volume)

    def play(self) -> None:
        """Start (or restart) playback in the background."""
        self._pb.play()

    def is_playing(self) -> bool:
        """True while loaded and not stopped/finished (includes paused)."""
        return self._pb.active

    def wait(self) -> None:
        """Block until playback finishes (never returns while looping)."""
        while self._pb.active:
            time.sleep(0.05)

    def pause(self) -> None:
        """Suspend playback, keeping position."""
        self._pb.pause()

    def resume(self) -> None:
        """Resume playback after pause()."""
        self._pb.resume()

    def set_volume(self, volume: float) -> None:
        """Set volume in the range 0.0–1.0."""
        self._pb.set_volume(volume)

    def stop(self) -> None:
        """Stop playback immediately, ending any loop."""
        self._pb.stop()
