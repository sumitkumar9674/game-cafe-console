# Game Cafe Console

Windows-only, local-first gaming café management. The same Python application runs on every PC: an Admin Dashboard on the Windows Default desktop, or a User Console on the separate Win32 CafeConsole desktop. PySide6/Qt Quick supplies all production screens; the LAN, security, SQLite, session, and Win32 desktop services remain Python-based.

## Run locally

Use Python 3.11 or newer on Windows. Install dependencies explicitly, then launch one controller per PC:

```powershell
python -m pip install -r requirements.txt
python .\run_game_cafe.py
```

Do not launch `--console-child` directly. The controller creates the child process on CafeConsole. To isolate test data, set `GAME_CAFE_DATA_DIR` to a dedicated directory before launch. Normal data stays in `%LOCALAPPDATA%\GameCafeConsole\console.sqlite3`; controller and child logs are written alongside it. The UI migration does not reset existing databases, identities, memberships, sessions, or history.

`cryptography` is required for authenticated encryption during pairing; the standard library has no equivalent primitive. `PySide6` supplies Qt Quick, QML, Controls, and native Windows rendering, which the standard library cannot provide.

## Supervised two-PC check

1. Back up each PC's `%LOCALAPPDATA%\GameCafeConsole` folder. On PC 1, launch the application, create a café, and confirm the Admin Dashboard appears on Default with no User Console on that PC.
2. Allow the application on the Private LAN if Windows Firewall prompts. UDP 48120 handles discovery; TCP 48121 handles pairing and authenticated commands. Do not expose these ports to the internet.
3. On PC 2, launch the same build. Discover the café and request to join. On PC 1, open Connections, start pairing, compare the seven-character code shown on PC 2, then approve. Verify PC 2 enters CafeConsole; a code expires after five minutes and five wrong entries reject that request.
4. From PC 1, start a timed session on PC 2 with buffer and Custom Minutes. Test player rename, Pause/Resume during buffer and paid time, custom Add Time with both confirmations, End Session, lock, and history filtering. Repeat with a No-Timer session and verify paused time is excluded.
5. While PC 2 has a session, disconnect or close the Admin. Confirm PC 2 stays usable, can rename/end locally, and later synchronizes history to the returning Admin. Simulate an abrupt controller termination only on a supervised test machine; verify restart finalizes at the last checkpoint, not the whole outage.
6. Check the Default desktop widget, tray hide/restore, café avatar Browse/preview/synchronization, and offline PC visibility. Test Admin transfer only when no customer session is active. Test local Close Software with password and remote Exit Software with confirmation; verify Default is restored. The exited PC no longer enforces café access until restarted.

Do not automate desktop switches or Windows sign-out during development. An exclusive fullscreen game may cover the widget; it does not inject into games or use hooks.

## Build

Install PyInstaller in the build environment, then run:

```powershell
python -m pip install pyinstaller
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_game_cafe.ps1
```

The result is the **whole** `dist\GameCafeConsole\` folder, including `GameCafeConsole.exe`, Qt DLLs/plugins, and QML resources. Copy the whole folder—not only the EXE—to another Windows PC. The same EXE re-launches as the CafeConsole child. The one-folder layout is suitable for later installer work; do not ship a café's SQLite database inside it. Rebuild and update both PCs together when testing protocol/UI changes.

Optional branding belongs in `game_cafe\assets\branding`: use `app_icon.ico`
for the EXE/title/taskbar icon and `app_logo.png` for the startup/Admin-login
loading screen. The build discovers the icon automatically and packages the
assets directory. Missing or invalid artwork safely uses the standard icon and
the existing StickForYou text fallback.

PyInstaller and Qt's `pyside6-deploy` are both viable deployment routes. This project keeps PyInstaller for now because its existing Windows child-process re-exec path works with a one-folder bundle and the QML files can be included explicitly. A packaged build still needs a supervised two-PC smoke test, especially Qt Quick rendering on CafeConsole. The child selects Qt Quick's software graphics API before its first window as a compatibility fallback; confirm its appearance and performance on target machines.

For commercial distribution, review the exact PySide6/Qt modules and their LGPLv3/GPLv3 or commercial license terms, attribution/notice, and applicable redistribution and relinking obligations. Bundling Qt DLLs alone does not settle compliance. Get qualified legal review before client release.

## Safety and current limits

CafeConsole is a UI/process-level restriction, not an OS security boundary. A customer with control of the shared Windows user account can potentially stop the process or inspect local data. A protected service/account and OS permissions would be required for stronger enforcement. The software never changes Registry policy, Group Policy, keyboard hooks, or Windows sign-out settings. Automatic sign-out remains disabled pending a recovery design.

If the child exits, the controller attempts to restore Default; if the controller dies, the child attempts to restore Default. Close Software on a User PC requires the Admin password; remote Exit Software requires an active Admin claim and explicit confirmation. The target PC owns its signed session record, persists changes locally before acknowledgment, and shares newer owner versions with peers. Existing sessions continue when the Admin is offline. A one-minute checkpoint limits the recoverable usage estimate after abrupt power loss; it is approximate, not proof of exactly when power failed. A remote timeout means completion is **unconfirmed**, even if the target may subsequently disconnect. During a network partition, competing authenticated Admin claims can briefly coexist; conflicting Admin edits may be lost.

The short pairing code is a human identity-check step, not a permanent secret. This is UI/process-level control: a person who controls the Windows account or local PC identity key can tamper with local data; stronger guarantees require OS permissions/services outside this implementation. Pre-checkpoint sessions from an older build are retained on first migration, so an interruption that happened **before** this version cannot be reconstructed precisely. Abnormal LAN partitions, changing IPs, clock skew, and Qt Quick rendering on real custom desktops require further supervised validation.

Developed by Sumit Kumar · StickForYou — [website](https://sfysumit.app) · [email](mailto:contact@sfysumit.app).
