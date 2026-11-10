; TunerBox Windows installer (Inno Setup 6). Built by packaging\build.py.
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef Root
  #define Root ".."
#endif

[Setup]
AppId={{6F0C7B1E-2C4B-4A57-9D0E-7B54A1C0B0E1}
AppName=TunerBox
AppVersion={#AppVersion}
AppPublisher=TunerBox
; All users: C:\Program Files\TunerBox (needs admin). "Install for me only" in the first dialog
; still puts it in %LOCALAPPDATA%\Programs\TunerBox without admin. Settings and recordings are
; always in %LOCALAPPDATA%\TunerBox, so they don't move either way.
DefaultDirName={autopf}\TunerBox
DefaultGroupName=TunerBox
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
; without this, upgrading a per-user install (1.4.2 and older) would stay per user in AppData
UsePreviousPrivileges=no
OutputDir={#Root}\dist
OutputBaseFilename=TunerBox-Setup-{#AppVersion}
SetupIconFile={#Root}\build\tunerbox.ico
UninstallDisplayIcon={app}\TunerBox.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#Root}\dist\TunerBox\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\TunerBox"; Filename: "{app}\TunerBox.exe"
Name: "{group}\TunerBox (server only)"; Filename: "{app}\TunerBox.exe"; Parameters: "--server"
Name: "{autodesktop}\TunerBox"; Filename: "{app}\TunerBox.exe"; Tasks: desktopicon

[Run]
; runasoriginaluser: an all-users install runs as admin, but TunerBox itself shouldn't
Filename: "{app}\TunerBox.exe"; Description: "Start TunerBox"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM TunerBox.exe"; Flags: runhidden; RunOnceId: "KillTunerBox"

; Your channels, settings and recordings stay in %LOCALAPPDATA%\TunerBox after uninstalling.

[Code]
// Versions up to 1.4.2 installed per user in %LOCALAPPDATA%\Programs\TunerBox. An all-users install
// removes that copy first, so there aren't two TunerBoxes in the Start menu. (Settings, channels and
// recordings are in %LOCALAPPDATA%\TunerBox and aren't touched by uninstalling.)
const
  UninstKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6F0C7B1E-2C4B-4A57-9D0E-7B54A1C0B0E1}_is1';

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Cmd: String;
  Code: Integer;
begin
  Result := '';
  if IsAdminInstallMode and RegQueryStringValue(HKCU, UninstKey, 'UninstallString', Cmd) then
  begin
    Log('Removing the per-user install: ' + Cmd);
    if not Exec(RemoveQuotes(Cmd), '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE,
                ewWaitUntilTerminated, Code) then
      Log('Could not run the old uninstaller (' + IntToStr(Code) + ')');
  end;
end;
