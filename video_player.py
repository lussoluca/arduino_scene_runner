"""Non-blocking fullscreen video playback using an mpv or ffplay subprocess.

Each player owns one player process, so the main thread stays free to
drive the Arduino. Fullscreen is forced; the window closes when the file
ends (unless looping). Pause/resume suspend the whole process with
SIGSTOP/SIGCONT, freezing both picture and sound.

The backend is auto-detected: mpv is preferred (cleaner fullscreen, no
dock bounce), ffplay (part of ffmpeg) is the fallback. Install either:

    brew install mpv
    brew install ffmpeg

Pass `backend="mpv"` or `backend="ffplay"` to force one, or set the
VIDEO_BACKEND environment variable.

Pass `screen=N` (index, 0 = first) or `screen="DELL U2715H"` (display
name, as reported by `--list-screens`) to pick the display for
fullscreen. Reliable with mpv; with ffplay only an integer index is
supported, as an SDL hint that some builds ignore.

A name that matches no connected display is not an error: mpv silently
falls back to whichever display is current. Names must match what
`--list-screens` prints, which for an AirPlay/Sidecar display includes
the suffix ("Sidecar Display (AirPlay)", not "Sidecar Display").

Example:
    from video_player import VideoPlayer

    player = VideoPlayer("clip.mp4", loop=True)
    player.play()
    while player.is_playing():
        ...   # drive the Arduino here
    player.stop()
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time

BACKENDS = ("mpv", "ffplay")


def default_backend() -> str:
    """Pick the best installed backend (env override: VIDEO_BACKEND)."""
    env = os.environ.get("VIDEO_BACKEND")
    if env:
        if env not in BACKENDS:
            raise ValueError(f"VIDEO_BACKEND must be one of {BACKENDS}, got {env!r}")
        return env
    for name in BACKENDS:
        if shutil.which(name):
            return name
    raise RuntimeError(
        "no video backend found: install mpv (`brew install mpv`) "
        "or ffmpeg (`brew install ffmpeg`)"
    )


def _probe_screen(index: int) -> str:
    """Open a black fullscreen mpv window on `index` and return the display
    name it actually landed on (via the `display-names` IPC property)."""
    # Socket path kept short: macOS limits unix socket paths to 104 bytes,
    # and the default TMPDIR (/var/folders/…) can push past that.
    sock_path = os.path.join(tempfile.mkdtemp(prefix="mpv-", dir="/tmp"), "s")
    proc = subprocess.Popen(
        [
            "mpv",
            "--fs",
            # Same placement pinning as playback, so the probe reports the
            # display the index really maps to and not one it drifted to.
            "--no-native-fs",
            f"--screen={index}",
            f"--fs-screen={index}",
            "--no-input-default-bindings",
            f"--input-ipc-server={sock_path}",
            "--loop-file=inf",
            # A real (black) video stream, so the probe window is created
            # exactly like normal playback windows.
            "av://lavfi:color=c=black:d=1",
        ],
        # Terminal output stays enabled and lands in these pipes, so a
        # failing mpv can be quoted in the error message.
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        sock = socket.socket(socket.AF_UNIX)
        for _ in range(50):  # wait for the IPC socket to appear
            try:
                sock.connect(sock_path)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if proc.poll() is not None:  # mpv died before serving IPC
                    out, err = proc.communicate()
                    said = (err + out).decode(errors="replace").strip()
                    raise RuntimeError(
                        f"mpv exited with code {proc.returncode} before its IPC "
                        f"socket appeared. mpv said:\n{said or '(nothing)'}"
                    )
                time.sleep(0.1)
        else:
            raise RuntimeError(
                "mpv is running but its IPC socket never appeared at "
                f"{sock_path} — mpv version too old for --input-ipc-server?"
            )
        time.sleep(0.5)  # let the window settle on the target screen
        sock.sendall(b'{"command": ["get_property", "display-names"]}\n')
        for line in sock.makefile():
            resp = json.loads(line)
            if "error" in resp:  # skip async events, keep the reply
                names = resp.get("data") or []
                break
        else:
            names = []
        name = names[0] if names else ""
        # mpv appends the display id: "DELL U2715H (810561619)".
        # `--fs-screen-name` wants the plain name, so strip it.
        if name.endswith(")"):
            name = name.rsplit(" (", 1)[0]
        return name
    finally:
        proc.terminate()
        proc.communicate()  # drain the pipes while reaping


def list_screens() -> list[str]:
    """Return display names indexed as mpv's `--fs-screen` sees them.

    Probes each index with a short-lived fullscreen mpv window (fullscreen
    windows cannot be moved between displays on macOS, so one window per
    index), so each screen flashes black for about a second. Stops at the
    first index that lands on an already-seen display. Requires mpv.
    """
    if not shutil.which("mpv"):
        raise RuntimeError("listing screens requires mpv (`brew install mpv`)")
    screens: list[str] = []
    for i in range(33):  # mpv accepts --fs-screen 0-32
        name = _probe_screen(i)
        if not name or name in screens:  # out of range: mpv falls back
            break
        screens.append(name)
    return screens


class VideoPlayer:
    """Fullscreen, controllable video playback for one file."""

    def __init__(
        self,
        path: str,
        loop: bool = False,
        volume: float = 1.0,
        backend: str | None = None,
        screen: int | str | None = None,
    ) -> None:
        self.path = path
        self.loop = loop
        self.volume = volume
        self.backend = backend or default_backend()
        if self.backend not in BACKENDS:
            raise ValueError(f"backend must be one of {BACKENDS}, got {self.backend!r}")
        self.screen = screen
        self._proc: subprocess.Popen | None = None
        self._paused = False

    def _command(self) -> list[str]:
        vol = int(max(0.0, min(1.0, self.volume)) * 100)
        if self.backend == "mpv":
            cmd = [
                "mpv",
                "--fs",
                "--no-terminal",
                "--really-quiet",
                "--no-input-default-bindings",
                # Bare picture: no on-screen controller (the seek bar that
                # pops up on mouse movement), no OSD text, no mouse cursor.
                "--no-osc",
                "--osd-level=0",
                "--cursor-autohide=always",
                f"--volume={vol}",
            ]
            if self.loop:
                cmd.append("--loop-file=inf")
            # --fs-screen* alone is unreliable on macOS: the window is
            # created on the target display, then the native-fullscreen
            # transition can migrate it to the main display (observed on
            # ~50% of launches onto an AirPlay/Sidecar display). Pinning the
            # initial window placement with --screen* and skipping native
            # fullscreen (no Space, no animation to race with) makes it land
            # on the requested display every time.
            if isinstance(self.screen, int):
                cmd += ["--no-native-fs",
                        f"--screen={self.screen}",
                        f"--fs-screen={self.screen}"]
            elif isinstance(self.screen, str):
                cmd += ["--no-native-fs",
                        f"--screen-name={self.screen}",
                        f"--fs-screen-name={self.screen}"]
        else:
            cmd = [
                "ffplay",
                "-fs",
                "-autoexit",
                "-loglevel", "error",
                "-volume", str(vol),
            ]
            if self.loop:
                cmd += ["-loop", "0"]  # 0 = loop forever
        cmd.append(self.path)
        return cmd

    def play(self) -> None:
        """Start (or restart) fullscreen playback in the background."""
        self.stop()
        env = None
        if isinstance(self.screen, int) and self.backend == "ffplay":
            # Best-effort SDL hint; not honored by every SDL build. Use the
            # mpv backend for reliable screen selection.
            env = {**os.environ, "SDL_VIDEO_FULLSCREEN_DISPLAY": str(self.screen)}
        self._proc = subprocess.Popen(
            self._command(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        self._paused = False

    def is_playing(self) -> bool:
        """True while the player process is alive (includes paused)."""
        return self._proc is not None and self._proc.poll() is None

    def wait(self) -> None:
        """Block until playback finishes (never returns while looping)."""
        while self.is_playing():
            time.sleep(0.05)

    def pause(self) -> None:
        """Suspend playback (picture and sound), keeping position."""
        proc = self._proc
        if proc is not None and proc.poll() is None and not self._paused:
            proc.send_signal(signal.SIGSTOP)
            self._paused = True

    def resume(self) -> None:
        """Resume playback after pause()."""
        proc = self._proc
        if proc is not None and proc.poll() is None and self._paused:
            proc.send_signal(signal.SIGCONT)
            self._paused = False

    def stop(self) -> None:
        """Stop playback and close the window immediately."""
        if self._proc is None:
            return
        if self._proc.poll() is None:
            if self._paused:  # a stopped process ignores SIGTERM
                self._proc.send_signal(signal.SIGCONT)
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        self._paused = False


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["--list-screens"]:
        for idx, screen_name in enumerate(list_screens()):
            print(f"{idx}: {screen_name}")
    else:
        sys.exit("usage: python video_player.py --list-screens")
