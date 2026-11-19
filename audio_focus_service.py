import logging
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import pywintypes
import win32api
import win32con
import win32event
import win32profile
import win32process
import win32service
import win32serviceutil
import win32ts


SERVICE_NAME = "AudioFocusService"
SCRIPT_DIR = Path(__file__).resolve().parent
LOG_DIR = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "AudioFocus"
SERVICE_LOG = LOG_DIR / "service.log"
PYTHON_EXECUTABLE = Path(sys.prefix) / "python.exe"
AGENT_STOP_TIMEOUT_MS = 10000
SUPERVISOR_INTERVAL_MS = 2000
LOG = logging.getLogger("audio-focus-service")


@dataclass
class AgentProcess:
    process_handle: object
    stop_file: Path
    process_id: int


class AudioFocusService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = "Audio Focus"
    _svc_description_ = (
        "Supervises per-user audio-focus agents for interactive Windows sessions."
    )

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, True, False, None)
        self.agents = {}
        self.retry_after = {}

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)

    def SvcShutdown(self):
        self.SvcStop()

    def SvcDoRun(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=str(SERVICE_LOG),
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            encoding="utf-8",
        )
        LOG.info("Audio Focus service started.")
        try:
            while win32event.WaitForSingleObject(self.stop_event, 0) != win32event.WAIT_OBJECT_0:
                try:
                    self._sync_agents()
                except pywintypes.error:
                    LOG.exception("Could not synchronize interactive-session agents.")
                if win32event.WaitForSingleObject(
                    self.stop_event, SUPERVISOR_INTERVAL_MS
                ) == win32event.WAIT_OBJECT_0:
                    break
        finally:
            self._stop_all_agents()
            LOG.info("Audio Focus service stopped.")

    def _active_session_ids(self):
        return {
            session["SessionId"]
            for session in win32ts.WTSEnumerateSessions(None, 1, 0)
            if session["State"] == win32ts.WTSActive
        }

    def _sync_agents(self):
        active_session_ids = self._active_session_ids()
        for session_id in list(self.agents):
            agent = self.agents[session_id]
            exited = (
                win32event.WaitForSingleObject(agent.process_handle, 0)
                == win32event.WAIT_OBJECT_0
            )
            if session_id not in active_session_ids or exited:
                if exited and session_id in active_session_ids:
                    exit_code = win32process.GetExitCodeProcess(agent.process_handle)
                    LOG.warning(
                        "Agent for session %s exited with code %s.",
                        session_id,
                        exit_code,
                    )
                    self.retry_after[session_id] = time.monotonic() + 10
                else:
                    self.retry_after.pop(session_id, None)
                self._stop_agent(session_id)

        for session_id in active_session_ids:
            if (session_id in self.agents
                    or time.monotonic() < self.retry_after.get(session_id, 0)):
                continue
            try:
                self.agents[session_id] = self._start_agent(session_id)
                LOG.info(
                    "Started agent for session %s (PID %s).",
                    session_id,
                    self.agents[session_id].process_id,
                )
            except (OSError, RuntimeError, pywintypes.error):
                LOG.exception("Could not start agent for session %s.", session_id)
                self.retry_after[session_id] = time.monotonic() + 10

        for session_id in set(self.retry_after) - active_session_ids:
            self.retry_after.pop(session_id, None)

    def _start_agent(self, session_id):
        token = win32ts.WTSQueryUserToken(session_id)
        environment = None
        process_handle = None
        thread_handle = None
        succeeded = False
        try:
            if not PYTHON_EXECUTABLE.is_file():
                raise RuntimeError(
                    f"Python interpreter not found at {PYTHON_EXECUTABLE}; "
                    f"service sys.executable is {sys.executable}."
                )
            environment = win32profile.CreateEnvironmentBlock(token, False)
            local_app_data = environment.get("LOCALAPPDATA")
            if not local_app_data:
                raise RuntimeError(
                    f"LOCALAPPDATA is unavailable for session {session_id}."
                )

            agent_directory = Path(local_app_data) / "AudioFocus"
            agent_log = agent_directory / "audio-focus.log"
            stop_file = agent_directory / f"stop-{uuid.uuid4().hex}.flag"

            arguments = [
                str(PYTHON_EXECUTABLE),
                str(SCRIPT_DIR / "audio_focus_agent.py"),
                "--stop-file",
                str(stop_file),
                "--log-file",
                str(agent_log),
            ]
            command_line = subprocess.list2cmdline(arguments)
            startup_info = win32process.STARTUPINFO()
            startup_info.dwFlags |= win32con.STARTF_USESHOWWINDOW
            startup_info.wShowWindow = win32con.SW_HIDE
            process_handle, thread_handle, process_id, _ = (
                win32process.CreateProcessAsUser(
                    token,
                    str(PYTHON_EXECUTABLE),
                    command_line,
                    None,
                    None,
                    False,
                    win32con.CREATE_NO_WINDOW | win32con.CREATE_UNICODE_ENVIRONMENT,
                    environment,
                    str(SCRIPT_DIR),
                    startup_info,
                )
            )
            win32api.CloseHandle(thread_handle)
            thread_handle = None
            succeeded = True
            return AgentProcess(process_handle, stop_file, process_id)
        finally:
            token.Close()
            if not succeeded:
                if process_handle is not None:
                    try:
                        win32process.TerminateProcess(process_handle, 1)
                    finally:
                        win32api.CloseHandle(process_handle)
                if thread_handle is not None:
                    win32api.CloseHandle(thread_handle)

    def _stop_agent(self, session_id):
        agent = self.agents.pop(session_id)
        try:
            agent.stop_file.parent.mkdir(parents=True, exist_ok=True)
            agent.stop_file.touch()
        except OSError:
            LOG.exception("Could not request graceful stop for session %s.", session_id)
        try:
            result = win32event.WaitForSingleObject(
                agent.process_handle, AGENT_STOP_TIMEOUT_MS
            )
            if result == win32event.WAIT_TIMEOUT:
                LOG.warning(
                    "Agent for session %s did not stop gracefully; terminating it.",
                    session_id,
                )
                win32process.TerminateProcess(agent.process_handle, 1)
                win32event.WaitForSingleObject(agent.process_handle, 1000)
        finally:
            win32api.CloseHandle(agent.process_handle)
            agent.stop_file.unlink(missing_ok=True)
        LOG.info("Stopped agent for session %s.", session_id)

    def _stop_all_agents(self):
        for session_id in list(self.agents):
            self._stop_agent(session_id)


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(AudioFocusService)
