#define MyAppName "GameGrid"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "StickForYou"
#define MyAppExeName "GameCafeConsole.exe"

; IMPORTANT: preserve this AppId in all future versions for safe installer upgrades.
; This is a *new* installer identity; the app's existing café IDs, SQLite folder,
; network protocol and internal executable name remain unchanged.
[Setup]
AppId={{00E4EF08-78E0-4E25-ABA3-95149B7CD6A1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\GameGrid
DefaultGroupName=GameGrid
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
OutputDir=release
OutputBaseFilename=GameGrid-Setup-1.0.0
SetupIconFile=installer\assets\gamegrid.ico
WizardStyle=modern
WizardImageFile=installer\assets\wizard_gamegrid.bmp
WizardSmallImageFile=installer\assets\wizard_small_gamegrid.bmp
DisableWelcomePage=no
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
UninstallDisplayIcon={app}\gamegrid.ico
CloseApplications=no
RestartApplications=no
SetupLogging=yes
VersionInfoDescription=GameGrid setup installer
VersionInfoProductName=GameGrid
VersionInfoVersion=1.0.0.0
VersionInfoCompany=StickForYou

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Install the ENTIRE PyInstaller one-folder output, including _internal.
Source: "dist\GameCafeConsole\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Keep a durable branded icon for Start menu/Desktop/uninstaller.
Source: "installer\assets\gamegrid.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\GameGrid"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\gamegrid.ico"
Name: "{autodesktop}\GameGrid"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\gamegrid.ico"; Tasks: desktopicon

[Code]
// Refuse to overwrite an application while a customer session or child process
// might still be running. This checks the packaged EXE name, not unrelated Python.
// It never force-closes a session or deletes café/user databases.
function GameGridMayBeRunning: Boolean;
var
  ResultCode: Integer;
  ReportFile, Args: String;
  ReportText: AnsiString;
begin
  // Fail closed if detection cannot be performed.
  Result := True;
  ReportFile := ExpandConstant('{tmp}\gamegrid_tasklist_check.txt');
  DeleteFile(ReportFile);
  Args := '/C tasklist.exe /FI "IMAGENAME eq GameCafeConsole.exe" /FO CSV /NH > "' + ReportFile + '"';
  if not Exec(ExpandConstant('{cmd}'), Args, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Exit;
  if ResultCode <> 0 then
    Exit;
  if not LoadStringFromFile(ReportFile, ReportText) then
    Exit;
  Result := Pos('gamecafeconsole.exe', Lowercase(ReportText)) > 0;
end;

function InitializeSetup: Boolean;
begin
  Result := not GameGridMayBeRunning;
  if not Result then
    MsgBox('GameGrid appears to be running, or its process status could not be checked.' + #13#10 +
      'Please end any active gaming session and close GameGrid normally on the Default Windows desktop, then retry setup.',
      mbError, MB_OK);
end;

function InitializeUninstall: Boolean;
begin
  Result := not GameGridMayBeRunning;
  if not Result then
    MsgBox('Close GameGrid safely before uninstalling it. Existing café data will not be deleted.', mbError, MB_OK);
end;

// IMPORTANT: Do NOT add [UninstallDelete] rules for %LOCALAPPDATA%\GameCafeConsole.
// Identity, sessions and signed history intentionally survive reinstall/uninstall.