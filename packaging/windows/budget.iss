; Inno Setup script for the Budget app. Built by packaging/build.py, which passes:
;   /DAppVersion=x.y.z /DSourceDir=<PyInstaller output folder> /DOutputDir=<dist> /DIconFile=<icon.ico>

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1B7C2E-3D4A-4E8B-9A51-2C7D0B8E4F93}
AppName=Budget
AppVersion={#AppVersion}
AppPublisher=Budget
DefaultDirName={autopf}\Budget
DefaultGroupName=Budget
DisableProgramGroupPage=yes
; per-user install: no administrator rights required
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#OutputDir}
OutputBaseFilename=Budget-{#AppVersion}-Windows-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\Budget.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Budget"; Filename: "{app}\Budget.exe"
Name: "{group}\{cm:UninstallProgram,Budget}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Budget"; Filename: "{app}\Budget.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Budget.exe"; Description: "{cm:LaunchProgram,Budget}"; Flags: nowait postinstall skipifsilent

; The user's data in %APPDATA%\Budget is intentionally kept on uninstall.
