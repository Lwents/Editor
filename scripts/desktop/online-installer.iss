#define AppName "Lwent Editor"
#include "installer-cache.iss"
#include DataInclude
[Setup]
AppId=LwentEditorOnlineInstaller
AppName={#AppName}
AppVersion={#UpdateVersion}
DefaultDirName={localappdata}\Programs\Editor
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
MinVersion=10.0
OutputDir={#Output}
OutputBaseFilename=Lwent-Editor-Online-Setup
SetupIconFile=assets\app-logo.ico
WizardStyle=modern
Uninstallable=no
CreateAppDir=no
DisableProgramGroupPage=yes
Compression=lzma2
[Files]
Source: "online-finalize.py"; Flags: dontcopy
Source: "apply-update.py"; Flags: dontcopy
Source: "{#Output}\Lwent-Uninstall-Maintenance.exe"; Flags: dontcopy
[Run]
Filename: "{app}\Editor.exe"; Description: "Mở Lwent Editor"; Flags: nowait postinstall skipifsilent
[Code]
var DownloadPage: TDownloadWizardPage;
procedure InitializeWizard;
begin
  WizardForm.WelcomeLabel2.Caption := 'Bộ cài sẽ tải dữ liệu từ GitHub rồi tự cài Lwent Editor. Cần Internet và dung lượng trống cho dữ liệu tải về cùng ứng dụng. Hãy đóng Lwent Editor trước khi cài.';
  DownloadPage := CreateDownloadPage('Đang tải Lwent Editor', 'Đang tải và kiểm tra dữ liệu cài đặt. Có thể mất thời gian tùy tốc độ mạng.', nil);
  DownloadPage.ShowBaseNameInsteadOfUrl := True;
end;
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID <> wpReady then exit;
  DownloadPage.Clear;
  AddInstallationDownloads(DownloadPage);
  DownloadPage.Show;
  try
    try
      DownloadPage.Download;
      PreserveRuntimeDownloads;
    except
      Result := False;
      if not DownloadPage.AbortedByUser then
        SuppressibleMsgBox('Chưa tải được dữ liệu. Kiểm tra kết nối rồi bấm Cài đặt để thử lại.' + #13#10 + GetExceptionMessage, mbError, MB_OK, IDOK);
    end;
  finally
    DownloadPage.Hide;
  end;
end;
procedure CurStepChanged(CurStep: TSetupStep);
var ExitCode: Integer; Python, Parameters, RuntimeSetup, RuntimeLog: String;
begin
  if CurStep <> ssInstall then exit;
  WizardForm.StatusLabel.Caption := 'Đang cài Lwent Editor…';
  RuntimeSetup := InstallerCache + '\Editor-Setup-Windows-x64.exe';
  if not RuntimeCacheComplete then RuntimeSetup := ExpandConstant('{tmp}\Editor-Setup-Windows-x64.exe');
  ForceDirectories(ExpandConstant('{localappdata}\LwentEditor\InstallerLogs'));
  RuntimeLog := ExpandConstant('{localappdata}\LwentEditor\InstallerLogs\runtime-') + GetDateTimeString('yyyymmdd-hhnnss', '-', ':') + '.log';
  if not Exec(RuntimeSetup, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /DIR="' + ExpandConstant('{app}') + '" /LOG="' + RuntimeLog + '"', ExtractFileDir(RuntimeSetup), SW_HIDE, ewWaitUntilTerminated, ExitCode) then
    RaiseException('Không chạy được bộ cài: ' + SysErrorMessage(ExitCode));
  if (ExitCode <> 0) and (ExitCode <> 3010) then
    RaiseException('Bộ cài thất bại, mã lỗi: ' + IntToStr(ExitCode) + '.' + #13#10 +
      'Dữ liệu cá nhân được giữ nguyên. File tải đã được giữ để thử lại.' + #13#10 +
      'Nhật ký lỗi: ' + RuntimeLog);
  ExtractTemporaryFile('online-finalize.py');
  ExtractTemporaryFile('apply-update.py');
  Python := ExpandConstant('{app}\resources\bundle\python\python.exe');
  Parameters := '"' + ExpandConstant('{tmp}\online-finalize.py') + '" --zip "' + ExpandConstant('{tmp}\Editor-Update-Windows-x64.zip') + '" --sha256 {#UpdateHash} --version {#UpdateVersion} --resources "' + ExpandConstant('{app}\resources') + '"';
  WizardForm.StatusLabel.Caption := 'Đang áp dụng phiên bản mới nhất…';
  if not Exec(Python, Parameters, '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then
    RaiseException('Không chạy được bước cập nhật: ' + SysErrorMessage(ExitCode));
  if ExitCode <> 0 then
    RaiseException('Đã cài bản nền nhưng chưa áp dụng được cập nhật. Mã lỗi: ' + IntToStr(ExitCode) + '. Có thể mở app và chọn Kiểm tra cập nhật.');
  ExtractTemporaryFile('Lwent-Uninstall-Maintenance.exe');
  WizardForm.StatusLabel.Caption := 'Đang cập nhật bộ gỡ cài đặt…';
  if not Exec(ExpandConstant('{tmp}\Lwent-Uninstall-Maintenance.exe'),
    '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="' + ExpandConstant('{app}') + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then
    RaiseException('Không cập nhật được bộ gỡ cài đặt: ' + SysErrorMessage(ExitCode));
  if ExitCode <> 0 then
    RaiseException('Không cập nhật được bộ gỡ cài đặt. Mã lỗi: ' + IntToStr(ExitCode));
  DelTree(InstallerCache, True, True, True);
end;
