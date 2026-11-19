# Audio Focus

This Windows-only product gives a newly audible audio session focus. When
Windows exposes a matching media-control session, it pauses the previous app and
resumes it at the same playback position when focus returns. Apps without
usable media controls are muted as a fallback, then restored to their original
mute state.

It uses each audio session's peak meter so a browser can release focus when
playback becomes silent even if its audio session stays open. Focus returns
after 0.8 seconds of silence by default.

## Run interactively

```powershell
python -m pip install -r requirements.txt
python audio_focus.py --dry-run
```

`--dry-run` logs intended pause/resume or mute actions without changing audio.
Remove it to enable audio focus. Use `--silence-timeout` to adjust how long
audio must be quiet before focus returns:

```powershell
python audio_focus.py
```

Press Ctrl+C to stop. The monitor polls every 200 ms by default; adjust this
with `--interval`. The default silence timeout is 0.8 seconds.

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

Apps already audible when an agent starts are left unchanged if more than one
is playing, because their relative start times cannot be determined reliably.
