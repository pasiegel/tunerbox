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
DefaultDirName={localappdata}\Programs\TunerBox
DefaultGroupName=TunerBox
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
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
Filename: "{app}\TunerBox.exe"; Description: "Start TunerBox"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM TunerBox.exe"; Flags: runhidden; RunOnceId: "KillTunerBox"

; Your channels, settings and recordings stay in %LOCALAPPDATA%\TunerBox after uninstalling.
