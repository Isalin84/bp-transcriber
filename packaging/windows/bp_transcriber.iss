; Inno Setup 6.3+ - установщик BP Transcriber для Windows 10/11 x64.
;
; Сборка (из корня репозитория, после `python packaging/build_app.py --clean`):
;   iscc /DAppVersion=1.0.0 packaging\windows\bp_transcriber.iss
;
; Необязательные параметры:
;   /DWizardImage=<bmp[,bmp@2x]>       боковая картинка мастера (164x314 и 328x628)
;   /DWizardSmallImage=<bmp[,bmp@2x]>  маленькая картинка в шапке (55x58 и 110x116)
; Если packaging\windows\redist\MicrosoftEdgeWebview2Setup.exe лежит рядом, он вшивается
; в установщик и запускается, когда WebView2 Runtime в системе не найден.

#ifndef AppVersion
  #error Укажите версию: iscc /DAppVersion=1.0.0 packaging\windows\bp_transcriber.iss
#endif

#define AppName       "BP Transcriber"
#define AppExeName    "BP Transcriber.exe"
#define AppPublisher  "Best Practice AI"
#define RepoRoot      "..\.."
#define WebView2Setup "redist\MicrosoftEdgeWebview2Setup.exe"

#if FileExists(SourcePath + WebView2Setup)
  #define HaveWebView2Setup
#else
  #pragma message "WebView2 bootstrapper не найден: установщик не сможет доустановить WebView2 Runtime."
#endif

[Setup]
; Фиксированный идентификатор приложения: по нему работают обновление и удаление.
; Скобка "{" в начале удваивается - так требует Inno Setup.
AppId={{3831340A-7758-4F31-AF4C-FE73CCD2BF9E}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://bestpracticeai.ru
AppSupportURL=https://github.com/Isalin84/bp-transcriber
AppUpdatesURL=https://github.com/Isalin84/bp-transcriber/releases
AppCopyright=MIT License. Best Practice AI

; {autopf} = %LOCALAPPDATA%\Programs при установке без прав администратора (по умолчанию) и Program Files для всех пользователей.
DefaultDirName={autopf}\BP Transcriber
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

Compression=lzma2/ultra64
SolidCompression=yes
LZMANumBlockThreads=4
DiskSpanning=no

WizardStyle=modern
SetupIconFile={#RepoRoot}\packaging\icons\bp.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
#ifdef WizardImage
WizardImageFile={#WizardImage}
#endif
#ifdef WizardSmallImage
WizardSmallImageFile={#WizardSmallImage}
#endif

OutputDir={#RepoRoot}\dist
OutputBaseFilename=BP-Transcriber-{#AppVersion}-windows-x64-setup

VersionInfoDescription={#AppName} Setup
VersionInfoProductName={#AppName}
VersionInfoCompany={#AppPublisher}

CloseApplications=yes
RestartApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
russian.WebView2Installing=Установка Microsoft Edge WebView2 Runtime...
english.WebView2Installing=Installing Microsoft Edge WebView2 Runtime...
russian.WebView2Failed=Не удалось установить Microsoft Edge WebView2 Runtime, без него окно приложения не откроется.%n%nУстановите его вручную: https://developer.microsoft.com/microsoft-edge/webview2/ и запустите BP Transcriber снова.
english.WebView2Failed=Microsoft Edge WebView2 Runtime could not be installed; the app window will not open without it.%n%nInstall it manually from https://developer.microsoft.com/microsoft-edge/webview2/ and start BP Transcriber again.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#RepoRoot}\dist\BP Transcriber\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#RepoRoot}\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#RepoRoot}\NOTICE.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
#ifdef HaveWebView2Setup
Source: "{#WebView2Setup}"; Flags: dontcopy
#endif

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

; Удаление: Inno убирает только файлы, которые сам установил. Пользовательские данные
; (настройки, история, модели, логи в %APPDATA% и %LOCALAPPDATA%) намеренно не трогаются.

[Code]
const
  WebView2ClientKey = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function IsRealVersion(const Value: String): Boolean;
begin
  Result := (Value <> '') and (Value <> '0.0.0.0');
end;

{ WebView2 Evergreen Runtime: значение pv в ключе EdgeUpdate (HKLM для x64-системы лежит в WOW6432Node, либо HKCU). }
function WebView2Installed: Boolean;
var
  Version: String;
begin
  Result := False;
  if RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\' + WebView2ClientKey, 'pv', Version) and IsRealVersion(Version) then
    Result := True
  else if RegQueryStringValue(HKCU, 'Software\' + WebView2ClientKey, 'pv', Version) and IsRealVersion(Version) then
    Result := True;
end;

#ifdef HaveWebView2Setup
procedure InstallWebView2;
var
  Installer: String;
  ResultCode: Integer;
begin
  if WebView2Installed then
    Exit;

  WizardForm.StatusLabel.Caption := CustomMessage('WebView2Installing');
  WizardForm.ProgressGauge.Style := npbstMarquee;
  try
    ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
    Installer := ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe');
    { Установщик сам запросит права администратора (UAC), если они нужны. }
    if not Exec(Installer, '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      Log('WebView2: не удалось запустить установщик, код ' + IntToStr(ResultCode))
    else
      Log('WebView2: установщик завершился с кодом ' + IntToStr(ResultCode));
  finally
    WizardForm.ProgressGauge.Style := npbstNormal;
  end;

  if not WebView2Installed then
    MsgBox(CustomMessage('WebView2Failed'), mbError, MB_OK);
end;
#endif

procedure CurStepChanged(CurStep: TSetupStep);
begin
#ifdef HaveWebView2Setup
  if CurStep = ssPostInstall then
    InstallWebView2;
#endif
end;
