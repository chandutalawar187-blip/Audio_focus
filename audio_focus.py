import asyncio
import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

if sys.platform != "win32":
    raise SystemExit("Audio Focus prototype requires Windows.")

try:
    from pycaw.pycaw import AudioUtilities
except ImportError as exc:
    raise SystemExit("Install dependencies with: python -m pip install -r requirements.txt") from exc


LOG = logging.getLogger("audio-focus")


class WindowsMediaController:
    def __init__(self):
        try:
            from winrt.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as SessionManager,
                GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
            )
        except ImportError as exc:
            raise SystemExit(
                "Install dependencies with: python -m pip install -r requirements.txt"
            ) from exc

        self.playback_status = PlaybackStatus
        self.loop = asyncio.new_event_loop()
        self.manager = self.loop.run_until_complete(SessionManager.request_async())

    @staticmethod
    def _matches(session, process_name):
        process_stem = os.path.splitext(os.path.basename(process_name))[0].casefold()
        return bool(process_stem) and process_stem in (
            session.source_app_user_model_id or ""
        ).casefold()

    def is_playing(self, process_name):
        return any(
            self._matches(session, process_name)
            and session.get_playback_info().playback_status
            == self.playback_status.PLAYING
            for session in self.manager.get_sessions()
        )

    def can_pause(self, process_name):
        for session in self.manager.get_sessions():
            if not self._matches(session, process_name):
                continue
            info = session.get_playback_info()
            if (info.playback_status == self.playback_status.PLAYING
                    and info.controls.is_pause_enabled):
                return True
        return False

    def pause(self, process_id, process_name):
        async def pause_session(session):
            return await session.try_pause_async()

        paused = []
        completely_paused = True
        matched_playing = False
        for session in self.manager.get_sessions():
            if not self._matches(session, process_name):
                continue
            info = session.get_playback_info()
            if info.playback_status != self.playback_status.PLAYING:
                continue
            matched_playing = True
            if not info.controls.is_pause_enabled:
                completely_paused = False
                continue
            if self.loop.run_until_complete(pause_session(session)):
                paused.append(session)
                LOG.info("Paused %s at its current position (PID %s)",
                         process_name, process_id)
            else:
                completely_paused = False
                LOG.warning("Windows could not pause %s (PID %s)",
                            process_name, process_id)
        return paused, matched_playing and completely_paused

    def resume(self, sessions, process_id, process_name):
        async def resume_session(session):
            return await session.try_play_async()

        for session in sessions:
            if self.loop.run_until_complete(resume_session(session)):
                LOG.info("Resumed %s at its previous position (PID %s)",
                         process_name, process_id)
            else:
                LOG.warning("Windows could not resume %s (PID %s)",
                            process_name, process_id)

    def close(self):
        self.loop.close()


@dataclass
class Session:
    instance_id: str
    process_id: int
    process_name: str
    audio: object
    active: bool


class AudioFocusMonitor:
    def __init__(self, dry_run=False, media_controller=None):
        self.dry_run = dry_run
        self.media_controller = media_controller
        self.focus_pid = None
        self.original_mute = {}
        self.applied_mute = set()
        self.process_names = {}
        self.paused_media = {}
        self.media_paused_pids = set()
        self.focus_stack = []
        self.playing_media_pids = set()
        self.playback_started_at = {}
        self.initialized = False
        self.startup_ambiguous = False

    @staticmethod
    def _sessions():
        sessions = []
        for session in AudioUtilities.GetAllSessions():
            process_id = session.ProcessId
            if not process_id or process_id == os.getpid():
                continue
            process = session.Process
            process_name = process.name() if process else f"PID {process_id}"
            sessions.append(
                Session(
                    session.InstanceIdentifier,
                    process_id,
                    process_name,
                    session.SimpleAudioVolume,
                    session.State == 1,  # AudioSessionStateActive
                )
            )
        return sessions

    def _set_muted(self, session, muted):
        key = session.instance_id
        if muted:
            if key in self.applied_mute:
                return
            if key not in self.original_mute:
                self.original_mute[key] = bool(session.audio.GetMute())
            if self.original_mute[key]:
                return
            target = True
            self.applied_mute.add(key)
        else:
            if key not in self.original_mute:
                return
            target = self.original_mute.pop(key)
            self.applied_mute.discard(key)

        if not self.dry_run:
            session.audio.SetMute(target, None)
        action = ("Would mute" if target else "Would unmute") if self.dry_run else (
            "Mute" if target else "Unmute"
        )
        LOG.info("%s %s (PID %s)", action, session.process_name, session.process_id)

    def _apply_focus(self, sessions):
        for session in sessions:
            is_media_playing = (
                session.active and self.media_controller is not None
                and self.media_controller.is_playing(session.process_name)
            )
            self._set_muted(
                session,
                session.active and self.focus_pid is not None
                and session.process_id != self.focus_pid
                and is_media_playing
                and session.process_id not in self.media_paused_pids,
            )

    def _change_focus(self, next_focus):
        previous_focus = self.focus_pid
        if previous_focus == next_focus:
            return

        if next_focus is None:
            LOG.info("Media focus released from %s (PID %s)",
                     self.process_names.get(previous_focus, f"PID {previous_focus}"),
                     previous_focus)
        else:
            LOG.info("Media focus -> %s (PID %s)",
                     self.process_names.get(next_focus, f"PID {next_focus}"),
                     next_focus)

        if previous_focus is not None and self.media_controller is not None:
            process_name = self.process_names.get(
                previous_focus, f"PID {previous_focus}"
            )
            if self.dry_run:
                completely_paused = self.media_controller.can_pause(process_name)
                if completely_paused:
                    LOG.info("Would pause %s at its current position (PID %s)",
                             process_name, previous_focus)
            else:
                paused, completely_paused = self.media_controller.pause(
                    previous_focus, process_name
                )
                if paused:
                    self.paused_media[previous_focus] = paused
            if completely_paused:
                self.media_paused_pids.add(previous_focus)

        if next_focus is not None:
            if previous_focus is not None:
                self.focus_stack.append(previous_focus)
            if next_focus in self.focus_stack:
                self.focus_stack = self.focus_stack[:self.focus_stack.index(next_focus)]
            if (next_focus in self.paused_media
                    or next_focus in self.media_paused_pids):
                media_sessions = self.paused_media.pop(next_focus, [])
                self.media_paused_pids.discard(next_focus)
                if self.dry_run:
                    LOG.info("Would resume %s at its previous position (PID %s)",
                             self.process_names.get(next_focus, f"PID {next_focus}"),
                             next_focus)
                elif media_sessions and self.media_controller is not None:
                    process_name = self.process_names.get(
                        next_focus, f"PID {next_focus}"
                    )
                    self.media_controller.resume(
                        media_sessions, next_focus, process_name
                    )

        self.focus_pid = next_focus

    def poll(self, now=None):
        now = time.monotonic() if now is None else now
        sessions = self._sessions()
        self.process_names.update(
            (session.process_id, session.process_name) for session in sessions
        )
        media_playing = [
            session for session in sessions
            if self.media_controller is not None
            and self.media_controller.is_playing(session.process_name)
        ]
        media_playing_pids = {session.process_id for session in media_playing}
        newly_playing_pids = list(
            dict.fromkeys(
                session.process_id
                for session in media_playing
                if session.process_id not in self.playing_media_pids
            )
        )
        for process_id in newly_playing_pids:
            self.playback_started_at[process_id] = now

        if not self.initialized:
            self.initialized = True
            if len(media_playing_pids) == 1:
                self._change_focus(next(iter(media_playing_pids)))
            else:
                self.startup_ambiguous = len(media_playing_pids) > 1
            self.playing_media_pids = media_playing_pids
            self._apply_focus(sessions)
            return

        self.playing_media_pids = media_playing_pids

        next_focus = self.focus_pid
        if self.startup_ambiguous and not newly_playing_pids:
            if len(media_playing_pids) == 1:
                next_focus = next(iter(media_playing_pids))
                self.startup_ambiguous = False
            else:
                next_focus = None
        elif newly_playing_pids:
            next_focus = newly_playing_pids[-1]
            self.startup_ambiguous = False
        elif self.focus_pid not in media_playing_pids:
            next_focus = next(
                (
                    session.process_id
                    for session in sorted(
                        media_playing,
                        key=lambda candidate: self.playback_started_at.get(
                            candidate.process_id, float("-inf")
                        ),
                        reverse=True,
                    )
                ),
                None,
            )
            if next_focus is None:
                next_focus = next(
                    (
                        process_id for process_id in reversed(self.focus_stack)
                        if process_id in self.paused_media
                        or process_id in self.media_paused_pids
                    ),
                    None,
                )

        self._change_focus(next_focus)
        self._apply_focus(sessions)

    def restore(self):
        for process_id, media_sessions in list(self.paused_media.items()):
            if self.media_controller is not None:
                process_name = self.process_names.get(
                    process_id, f"PID {process_id}"
                )
                self.media_controller.resume(
                    media_sessions, process_id, process_name
                )
        self.paused_media.clear()
        self.media_paused_pids.clear()
        for session in self._sessions():
            self._set_muted(session, False)


def main():
    parser = argparse.ArgumentParser(
        description="Give actively playing media focus; pause or mute competing apps."
    )
    parser.add_argument("--interval", type=float, default=0.2,
                        help="session polling interval in seconds (default: 0.2)")
    parser.add_argument("--dry-run", action="store_true",
                        help="log focus changes without changing audio")
    parser.add_argument("--log-file", help="write log output to this file")
    parser.add_argument("--stop-file", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be greater than zero")
    logging_options = {
        "level": logging.INFO,
        "format": "%(asctime)s %(levelname)s %(message)s",
    }
    if args.log_file:
        log_path = Path(args.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logging_options["filename"] = str(log_path)
        logging_options["encoding"] = "utf-8"
    logging.basicConfig(**logging_options)
    stop_file = Path(args.stop_file) if args.stop_file else None
    media_controller = WindowsMediaController()
    monitor = AudioFocusMonitor(
        dry_run=args.dry_run,
        media_controller=media_controller,
    )
    LOG.info("Monitoring active media playback. Press Ctrl+C to exit.")
    try:
        while True:
            monitor.poll()
            if stop_file is not None and stop_file.exists():
                break
            time.sleep(args.interval)
    finally:
        LOG.info("Stopping; restoring playback and audio session mute states.")
        try:
            monitor.restore()
        finally:
            media_controller.close()


if __name__ == "__main__":
    main()
