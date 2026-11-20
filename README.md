# Audio Focus

This Windows-only product gives focus to a newly playing media session. When
Windows exposes a matching media-control session, it pauses the previous app and
resumes it at the same playback position when focus returns. Apps without
pause support are muted as a fallback, then restored to their original mute
state. Only apps with a Windows media-control session actively reporting
playback are eligible; notification and other non-media sounds are ignored.

Focus follows Windows' media playback state, not audio peaks, so silent
intros and quiet passages still keep focus. When media pauses or stops, the
most recently preempted app is resumed.

## Run interactively

```powershell
python -m pip install -r requirements.txt
python audio_focus.py --dry-run
```

`--dry-run` logs intended pause/resume or mute actions without changing audio.
Remove it to enable audio focus:

```powershell
python audio_focus.py
```

Press Ctrl+C to stop. The monitor polls every 200 ms by default; adjust this
with `--interval`.

## Install as a Windows service

Move this folder to a permanent location before installing; the service refers
to the scripts in this folder. Open PowerShell as Administrator and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_service.ps1
```

The auto-start service supervises a hidden audio agent in each active user
session. The service runs as LocalSystem so it can start agents at sign-in; the
agent itself runs as the signed-in user because audio sessions are isolated by
Windows session. Service logs are written to
`C:\ProgramData\AudioFocus\service.log`; each user's agent log is under
`%LOCALAPPDATA%\AudioFocus\audio-focus.log`.

To apply updated code to an existing installation, rerun the elevated
`.\install_service.ps1` script. It stops and updates the service before
restarting it.

To uninstall, from an elevated PowerShell window run:

```powershell
.\uninstall_service.ps1
```

If multiple apps are already playing when an agent starts, it leaves them
unchanged until playback state changes because their relative start times
cannot be determined reliably.
