#define MyAppName "RexArticles"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif
#define MyAppPublisher "RexArticles"
#define MyAppExeName "RexArticles.exe"

[Setup]
AppId={{E98B78A5-6742-4E36-97E2-34AB8070BF63}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=release_windows
OutputBaseFilename=RexArticles-Setup-v{#MyAppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные значки:"; Flags: unchecked

[Files]
; The AppId and UsePreviousAppDir make a newer installer update the existing
; RexArticles installation. User data is deliberately never installed or
; overwritten, so it also remains after an update or uninstall.
Source: "dist\RexArticles\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "config.json,stores.json,manual_overrides.json,article_corrections.json,index\*,logs\*"

[Icons]
Name: "{group}\RexArticles"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\RexArticles"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить RexArticles"; Flags: nowait postinstall skipifsilent

[Code]
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    if (Pos(ExpandConstant('{pf}\'), WizardDirValue + '\') = 1) or
       (Pos(ExpandConstant('{pf32}\'), WizardDirValue + '\') = 1) or
       (Pos(ExpandConstant('{pf64}\'), WizardDirValue + '\') = 1) then
    begin
      Result := MsgBox(
        'RexArticles хранит настройки рядом с EXE. В Program Files для этого могут потребоваться права администратора.' + #13#10 + #13#10 +
        'Продолжить установку в выбранную папку?',
        mbConfirmation, MB_YESNO) = IDYES;
    end;
  end;
end;
