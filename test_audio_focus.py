import unittest

from audio_focus import AudioFocusMonitor, Session


class FakeAudio:
    def __init__(self, muted=False):
        self.muted = muted
        self.mute_changes = []

    def GetMute(self):
        return self.muted

    def SetMute(self, muted, _event_context):
        self.muted = muted
        self.mute_changes.append(muted)


class FakeMediaController:
    def __init__(self, playing=(), pause_supported=True):
        self.playing = set(playing)
        self.pause_supported = pause_supported
        self.paused = []
        self.resumed = []

    def is_playing(self, process_name):
        return process_name in self.playing

    def can_pause(self, process_name):
        return self.pause_supported and self.is_playing(process_name)

    def pause(self, process_id, process_name):
        if not self.is_playing(process_name):
            return [], False
        if not self.pause_supported:
            return [], False
        self.paused.append(process_id)
        self.playing.discard(process_name)
        return [process_id], True

    def resume(self, sessions, process_id, process_name):
        self.resumed.append((sessions, process_id))
        self.playing.add(process_name)


def make_session(instance_id, process_id, active=True, muted=False):
    return Session(
        instance_id,
        process_id,
        f"app-{process_id}",
        FakeAudio(muted),
        active,
    )


class AudioFocusMonitorTests(unittest.TestCase):
    def test_notification_sound_does_not_take_focus_or_pause_media(self):
        media = FakeMediaController({"app-101"})
        monitor = AudioFocusMonitor(media_controller=media)
        player = make_session("player", 101)
        notification = make_session("notification", 202)
        monitor._sessions = lambda: [player]

        monitor.poll(now=0.0)
        monitor._sessions = lambda: [player, notification]
        monitor.poll(now=0.2)

        self.assertEqual(monitor.focus_pid, 101)
        self.assertEqual(media.paused, [])
        self.assertEqual(player.audio.mute_changes, [])
        self.assertEqual(notification.audio.mute_changes, [])

    def test_dry_run_does_not_pause_media(self):
        media = FakeMediaController({"app-101", "app-202"})
        monitor = AudioFocusMonitor(dry_run=True, media_controller=media)
        previous = make_session("previous", 101)
        current = make_session("current", 202)
        monitor._sessions = lambda: [previous]

        monitor.poll(now=0.0)
        monitor._sessions = lambda: [previous, current]
        monitor.poll(now=0.2)

        self.assertEqual(media.paused, [])
        self.assertFalse(previous.audio.muted)

    def test_media_is_paused_and_resumed_at_existing_position(self):
        media = FakeMediaController({"app-101", "app-202"})
        monitor = AudioFocusMonitor(media_controller=media)
        previous = make_session("previous", 101)
        current = make_session("current", 202)
        monitor._sessions = lambda: [previous]

        monitor.poll(now=0.0)
        monitor._sessions = lambda: [previous, current]
        monitor.poll(now=0.2)
        self.assertEqual(media.paused, [101])
        self.assertFalse(previous.audio.muted)

        media.playing.discard("app-202")
        monitor.poll(now=1.1)
        self.assertEqual(monitor.focus_pid, 101)
        self.assertEqual(media.resumed, [([101], 101)])
        self.assertFalse(previous.audio.muted)

    def test_silent_media_takes_focus_and_keeps_it_while_playing(self):
        media = FakeMediaController({"app-101", "app-202"})
        monitor = AudioFocusMonitor(media_controller=media)
        previous = make_session("previous", 101)
        current = make_session("current", 202, active=False)
        monitor._sessions = lambda: [previous]

        monitor.poll(now=0.0)
        monitor._sessions = lambda: [previous, current]
        monitor.poll(now=0.2)
        self.assertEqual(monitor.focus_pid, 202)
        self.assertEqual(media.paused, [101])

        monitor.poll(now=2.0)

        self.assertEqual(monitor.focus_pid, 202)
        self.assertEqual(media.paused, [101])
        self.assertEqual(media.resumed, [])

    def test_new_session_mutes_previous_and_restores_it_when_focus_ends(self):
        media = FakeMediaController(
            {"app-101", "app-202"},
            pause_supported=False,
        )
        monitor = AudioFocusMonitor(media_controller=media)
        previous = make_session("previous", 101)
        current = make_session("current", 202)
        monitor._sessions = lambda: [previous]

        monitor.poll(now=0.0)
        self.assertFalse(previous.audio.muted)

        monitor._sessions = lambda: [previous, current]
        monitor.poll(now=1.0)
        self.assertTrue(previous.audio.muted)
        self.assertFalse(current.audio.muted)
        self.assertEqual(monitor.focus_pid, 202)

        monitor._sessions = lambda: [previous, current]
        media.playing.discard("app-202")
        monitor.poll(now=1.2)
        self.assertFalse(previous.audio.muted)
        self.assertEqual(monitor.focus_pid, 101)

    def test_ambiguous_audio_already_playing_at_start_is_left_unchanged(self):
        media = FakeMediaController({"app-101", "app-202"})
        monitor = AudioFocusMonitor(media_controller=media)
        first = make_session("first", 101)
        second = make_session("second", 202)
        monitor._sessions = lambda: [first, second]

        monitor.poll()
        monitor.poll()

        self.assertIsNone(monitor.focus_pid)
        self.assertFalse(first.audio.muted)
        self.assertFalse(second.audio.muted)


if __name__ == "__main__":
    unittest.main()
