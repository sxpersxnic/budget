; Inno Setup script for the Budget app. Built by packaging/build.py, which passes:
;   /DAppVersion=x.y.z /DSourceDir=<PyInstaller output folder> /DOutputDir=<dist> /DIconFile=<icon.ico>
;
; Updates: running a newer Setup.exe over an existing install updates it in place. The fixed AppId
; finds the previous install; its folder, language and tasks are reused, a running Budget is closed
; first, and the previous program files are removed so nothing stale survives. Saved as UTF-8 with
; BOM for the translated messages below.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1B7C2E-3D4A-4E8B-9A51-2C7D0B8E4F93}
AppName=Budget
AppVersion={#AppVersion}
AppVerName=Budget {#AppVersion}
VersionInfoVersion={#AppVersion}
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
; close a running Budget before its files are replaced (Windows Restart Manager); [Run] relaunches it
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[CustomMessages]
english.UpdateHeading=Update
english.UpdateFromTo=Budget %1 will be replaced by version %2.
english.Reinstall=Budget %1 will be installed again.
english.DataKept=Your data in %1 is kept.
german.UpdateHeading=Aktualisierung
german.UpdateFromTo=Budget %1 wird durch Version %2 ersetzt.
german.Reinstall=Budget %1 wird erneut installiert.
german.DataKept=Ihre Daten in %1 bleiben erhalten.
french.UpdateHeading=Mise à jour
french.UpdateFromTo=Budget %1 sera remplacé par la version %2.
french.Reinstall=Budget %1 sera réinstallé.
french.DataKept=Vos données dans %1 sont conservées.

[InstallDelete]
; The bundled Python libraries change between versions; clear the old ones so no stale module is loaded.
; User data lives in %APPDATA%\Budget, never in the program folder.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Budget"; Filename: "{app}\Budget.exe"
Name: "{group}\{cm:UninstallProgram,Budget}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Budget"; Filename: "{app}\Budget.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Budget.exe"; Description: "{cm:LaunchProgram,Budget}"; Flags: nowait postinstall skipifsilent

; The user's data in %APPDATA%\Budget is intentionally kept on uninstall.

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6F1B7C2E-3D4A-4E8B-9A51-2C7D0B8E4F93}_is1';

// Version of an existing install ('' when there is none), per-user or machine-wide.
function InstalledVersion(): String;
begin
  Result := '';
  if not RegQueryStringValue(HKCU, UninstallKey, 'DisplayVersion', Result) then
    RegQueryStringValue(HKLM, UninstallKey, 'DisplayVersion', Result);
end;

// An update keeps the previous choices, so only the Ready page remains before installing.
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = wpSelectTasks) and (InstalledVersion() <> '');
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  Previous: String;
begin
  Result := '';
  Previous := InstalledVersion();
  if Previous <> '' then
  begin
    Result := CustomMessage('UpdateHeading') + NewLine + Space;
    if Previous = '{#AppVersion}' then
      Result := Result + FmtMessage(CustomMessage('Reinstall'), [Previous])
    else
      Result := Result + FmtMessage(CustomMessage('UpdateFromTo'), [Previous, '{#AppVersion}']);
    Result := Result + NewLine + Space
      + FmtMessage(CustomMessage('DataKept'), [ExpandConstant('{userappdata}\Budget')]) + NewLine + NewLine;
  end;
  if MemoDirInfo <> '' then
    Result := Result + MemoDirInfo + NewLine + NewLine;
  if MemoGroupInfo <> '' then
    Result := Result + MemoGroupInfo + NewLine + NewLine;
  if MemoTasksInfo <> '' then
    Result := Result + MemoTasksInfo + NewLine;
end;
