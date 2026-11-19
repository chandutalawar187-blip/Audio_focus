import unittest

from audio_focus import AudioFocusMonitor, Session


class FakeAudio:
    def __init__(self, muted=False):
        self.muted = muted

    def GetMute(self):
        return self.muted

    def SetMute(self, muted, _event_context):
        self.muted = muted


class FakeMediaController:
    def __init__(self):
        self.paused = []
        self.resumed = []

    def can_pause(self, _process_name):
        return True

    def pause(self, process_id, _process_name):
        self.paused.append(process_id)
        return [process_id], True

    def resume(self, sessions, process_id, _process_name):
        self.resumed.append((sessions, process_id))


def make_session(instance_id, process_id, active=True, muted=False, peak=0.0):
    return Session(
        instance_id,
        process_id,
        f"app-{process_id}",
        FakeAudio(muted),
        active,
        peak,
    )


class AudioFocusMonitorTests(unittest.TestCase):
    def test_dry_run_does_not_pause_media(self):
        media = FakeMediaController()
        monitor = AudioFocusMonitor(dry_run=True, media_controller=media)
        previous = make_session("previous", 101, peak=0.5)
        current = make_session("current", 202, peak=0.5)
        monitor._sessions = lambda: [previous]

        monitor.poll(now=0.0)
        monitor._sessions = lambda: [previous, current]
        monitor.poll(now=0.2)

        self.assertEqual(media.paused, [])
        self.assertFalse(previous.audio.muted)

    def test_media_is_paused_and_resumed_at_existing_position(self):
        media = FakeMediaController()
        monitor = AudioFocusMonitor(media_controller=media)
        previous = make_session("previous", 101, peak=0.5)
        current = make_session("current", 202, peak=0.5)
        monitor._sessions = lambda: [previous]

        monitor.poll(now=0.0)
        monitor._sessions = lambda: [previous, current]
        monitor.poll(now=0.2)
        self.assertEqual(media.paused, [101])
        self.assertFalse(previous.audio.muted)

        current.peak = 0.0
        previous.peak = 0.0
        monitor.poll(now=1.1)
        self.assertEqual(monitor.focus_pid, 101)
        self.assertEqual(media.resumed, [([101], 101)])
        self.assertFalse(previous.audio.muted)

    def test_new_session_mutes_previous_and_restores_it_when_focus_ends(self):
        monitor = AudioFocusMonitor()
        previous = make_session("previous", 101, peak=0.5)
        current = make_session("current", 202, peak=0.5)
        monitor._sessions = lambda: [previous]

        monitor.poll(now=0.0)
        self.assertFalse(previous.audio.muted)

        monitor._sessions = lambda: [previous, current]
        monitor.poll(now=1.0)
        self.assertTrue(previous.audio.muted)
        self.assertFalse(current.audio.muted)
        self.assertEqual(monitor.focus_pid, 202)

        current.peak = 0.0
        monitor._sessions = lambda: [previous, current]
        monitor.poll(now=1.2)
        self.assertTrue(previous.audio.muted)

        monitor.poll(now=1.81)
        self.assertFalse(previous.audio.muted)
        self.assertEqual(monitor.focus_pid, 101)

    def test_ambiguous_audio_already_playing_at_start_is_left_unchanged(self):
        monitor = AudioFocusMonitor()
        first = make_session("first", 101, peak=0.5)
        second = make_session("second", 202, peak=0.5)
        monitor._sessions = lambda: [first, second]

        monitor.poll()
        monitor.poll()

        self.assertIsNone(monitor.focus_pid)
        self.assertFalse(first.audio.muted)
        self.assertFalse(second.audio.muted)


if __name__ == "__main__":
    unittest.main()
