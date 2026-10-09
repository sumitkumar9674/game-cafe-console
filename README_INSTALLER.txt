GAMEGRID BRANDED WINDOWS INSTALLER KIT (v1.0.0)

The kit contains the actual approved logo, a custom icon, branded setup wizard images, a reproducible Inno Setup script, and a build wrapper.

IMPORTANT: GameGrid-Setup-1.0.0.exe is NOT inside this ZIP. Build it on the project Windows machine with its source and dependencies.

PREPARATION
1. Install Inno Setup 6 (official: https://jrsoftware.org/isinfo.php).
2. Close GameGrid / GameCafeConsole on both Windows desktops before upgrading or building.
3. Extract this ZIP into the root of your existing project, the folder that has build_game_cafe.ps1. Merge folders, do not overwrite source files.
4. Confirm game_cafe/assets/branding/app_logo.png is the original approved GameGrid logo (as reported by Codex). The script verifies its SHA-256 hash against installer/assets/gamegrid_logo_reference.png.
5. From PowerShell in the project root run:
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_gamegrid_installer.ps1

OUTPUT
release\GameGrid-Setup-1.0.0.exe

The setup will install the existing PyInstaller onedir GameCafeConsole.exe, keep _internal, create a Start Menu entry and offer an optional desktop shortcut. The setup/uninstall checks for active packaged processes and refuses to overwrite running software. It never force-kills.

PC IDENTITIES AND DATABASE
- Do not copy or delete %LOCALAPPDATA%\GameCafeConsole\console.sqlite3.
- User PC identities and session history remain local to each PC.
- Installer does not create or overwrite the database and does not uninstall it.
- Installing over a separately extracted portable folder does not remove that old folder. Do not run both copies.

BRANDING SCOPE
- Setup wizard, setup icon, start menu and desktop shortcut use the provided gamegrid.ico artwork.
- The application itself uses app_logo.png already integrated into its QML.
- IMPORTANT: The inner GameCafeConsole.exe's embedded Windows icon depends on your existing build_game_cafe.ps1 / PyInstaller --icon configuration. This kit does NOT rewrite the embedded icon without seeing that script. If taskbar/exe still shows the old icon, update the build script's --icon argument to point to installer\assets\gamegrid.ico and rebuild. Preserve internal EXE name, mutex, DB path and protocols.

TESTS TO RUN AFTER INSTALL
1. Confirm GameGrid launches and the logo is visible.
2. Confirm PC identity and existing café history remain intact.
3. Verify Admin and User discovery, timed session and buffer colors.
4. Verify Staff Access and Close Software; ensure no GameCafeConsole.exe children remain.
5. Optionally repeat LAN two-PC smoke tests.

NOTE
The current kit is not code-signed. Windows SmartScreen may warn on unsigned applications. Do not bypass your organization's security policy.
