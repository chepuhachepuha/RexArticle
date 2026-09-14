#define MyAppName "RexArticles"
#define MyAppVersion "1.0.2"
#define MyAppPublisher "RexArticles"
#define MyAppExeName "RexArticles.exe"
#define MyAppId "{E98B78A5-6742-4E36-97E2-34AB8070BF63}"

[Setup]
AppId={{E98B78A5-6742-4E36-97E2-34AB8070BF63}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={code:GetExistingInstallDir}
DefaultGroupName={#MyAppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=release_windows
OutputBaseFilename=RexArticles_Update_v{#MyAppVersion}
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

[Files]
Source: "dist\RexArticles\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "config.json,stores.json,manual_overrides.json"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить RexArticles"; Flags: nowait postinstall skipifsilent

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}_is1';

var
  ExistingInstallDir: String;
  BackupDir: String;

function IsRexArticlesDirectory(const Dir: String): Boolean;
begin
  Result := FileExists(AddBackslash(Dir) + '{#MyAppExeName}');
end;

function FindExistingInstall(var Dir: String): Boolean;
begin
  Result := RegQueryStringValue(HKCU, UninstallKey, 'InstallLocation', Dir);
  if not Result then
    Result := RegQueryStringValue(HKLM32, UninstallKey, 'InstallLocation', Dir);
  if not Result and IsWin64 then
    Result := RegQueryStringValue(HKLM64, UninstallKey, 'InstallLocation', Dir);
  if Result then
  begin
    Dir := RemoveBackslashUnlessRoot(Dir);
    Result := IsRexArticlesDirectory(Dir);
  end;

  if not Result then
  begin
    Dir := RemoveBackslashUnlessRoot(ExpandConstant('{src}'));
    Result := IsRexArticlesDirectory(Dir);
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := FindExistingInstall(ExistingInstallDir);
  if not Result then
    MsgBox(
      'Не найдена существующая установка RexArticles.' + #13#10 + #13#10 +
      'Этот файл предназначен только для обновления и не создаёт новую установку.' + #13#10 +
      'Если используется portable-версия, поместите файл обновления рядом с RexArticles.exe и запустите снова.',
      mbError,
      MB_OK
    );
end;

function GetExistingInstallDir(Param: String): String;
begin
  Result := ExistingInstallDir;
end;

function BackupUserFile(const FileName: String): Boolean;
var
  SourceFile: String;
begin
  SourceFile := AddBackslash(ExistingInstallDir) + FileName;
  Result := True;
  if FileExists(SourceFile) then
    Result := CopyFile(SourceFile, AddBackslash(BackupDir) + FileName, False);
end;

procedure RestoreUserFile(const FileName: String);
var
  BackupFile: String;
begin
  BackupFile := AddBackslash(BackupDir) + FileName;
  if FileExists(BackupFile) then
    CopyFile(BackupFile, AddBackslash(ExistingInstallDir) + FileName, False);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  BackupDir := ExpandConstant('{tmp}\RexArticlesUpdateBackup');
  DelTree(BackupDir, True, True, True);
  if not ForceDirectories(BackupDir) then
  begin
    Result := 'Не удалось создать временную резервную копию настроек.';
    exit;
  end;

  if not BackupUserFile('config.json') or
     not BackupUserFile('stores.json') or
     not BackupUserFile('manual_overrides.json') then
    Result := 'Не удалось создать резервную копию пользовательских настроек. Обновление отменено.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    RestoreUserFile('config.json');
    RestoreUserFile('stores.json');
    RestoreUserFile('manual_overrides.json');
    DelTree(BackupDir, True, True, True);
  end;
end;