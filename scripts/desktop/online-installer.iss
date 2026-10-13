#define AppName "Lwent Editor"
#include "installer-cache.iss"
#include DataInclude
#include "installer-errors.iss"
[Code]
function GetCurrentProcessId: Integer;
  external 'GetCurrentProcessId@kernel32.dll stdcall';
#include "installer-download.iss"
#include "installer-process.iss"
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
CreateAppDir=yes
DisableDirPage=no
DisableProgramGroupPage=yes
Compression=lzma2
SetupLogging=yes
[Files]
Source: "{#DownloadTools}\aria2c.exe"; Flags: dontcopy
Source: "{#DownloadTools}\COPYING"; Flags: dontcopy
Source: "{#DownloadTools}\LICENSE.OpenSSL"; Flags: dontcopy
Source: "online-download.ps1"; Flags: dontcopy
Source: "installer-process.ps1"; Flags: dontcopy
Source: "{#DownloadManifest}"; DestName: "installation-downloads.json"; Flags: dontcopy
Source: "online-finalize.py"; Flags: dontcopy
Source: "apply-update.py"; Flags: dontcopy
Source: "{#Output}\Lwent-Uninstall-Maintenance.exe"; Flags: dontcopy
[Run]
Filename: "{app}\Editor.exe"; Description: "Mở Lwent Editor"; Flags: nowait postinstall skipifsilent
[Code]
procedure InitializeWizard;
begin
  StartInstallLog;
  InstallEvent('Khởi tạo bộ cài');
  WizardForm.WelcomeLabel2.Caption := 'Bộ cài sẽ tải dữ liệu từ GitHub rồi tự cài Lwent Editor. Cần Internet và dung lượng trống cho dữ liệu tải về cùng ứng dụng. Hãy đóng Lwent Editor trước khi cài.';
  FastDownloadPage := CreateOutputProgressPage('Đang tải Lwent Editor', 'Tải song song, tự tiếp tục phần đã tải và kiểm tra dữ liệu.');
  FastDownloadPage.Msg2Label.Height := ScaleY(60);
  FastDownloadPage.Msg2Label.AutoSize := False;
  FastDownloadPage.ProgressBar.Top := ScaleY(86);
  StopDownloadButton := TNewButton.Create(WizardForm);
  StopDownloadButton.Parent := FastDownloadPage.Surface;
  StopDownloadButton.SetBounds(0, ScaleY(140), ScaleX(140), ScaleY(28));
  StopDownloadButton.Caption := 'Dừng tải';
  StopDownloadButton.OnClick := @StopFastDownload;
end;
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID <> wpReady then exit;
  InstallEvent('Thư mục cài đã chọn: ' + ExpandConstant('{app}'));
  ErrorWasReported := False;
  InstallEvent('Bắt đầu tải và kiểm tra SHA256 dữ liệu');
  try
    Result := DownloadInstallation;
    if Result then InstallEvent('Đã tải và kiểm tra xong dữ liệu');
  except
    Result := False;
    if not ErrorWasReported then
      ReportInstallError('Chuẩn bị tải dữ liệu', 'DOWNLOAD', 1, GetExceptionMessage, '');
  end;
end;
procedure InstallDownloadedApplication;
var ExitCode: Integer; Python, Parameters, RuntimeSetup, RuntimeLog, UpdateLog, MaintenanceLog, RuntimeMode: String;
begin
  WizardForm.StatusLabel.Caption := 'Đang cài Lwent Editor…';
  InstallEvent('Bắt đầu cài runtime vào ' + ExpandConstant('{app}'));
  RuntimeSetup := InstallerCache + '\Editor-Setup-Windows-x64.exe';
  if not RuntimeCacheComplete then RaiseException('Cache runtime thiếu file sau khi tải.');
  ForceDirectories(ExpandConstant('{localappdata}\LwentEditor\InstallerLogs'));
  RuntimeLog := ExpandConstant('{localappdata}\LwentEditor\InstallerLogs\runtime-') + GetDateTimeString('yyyymmdd-hhnnss', '-', ':') + '.log';
  RuntimeMode := '/SILENT'; if WizardSilent then RuntimeMode := '/VERYSILENT';
  if not RunInstallStep(RuntimeSetup, RuntimeMode + ' /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /DIR="' + ExpandConstant('{app}') + '" /LOG="' + RuntimeLog + '"', ExtractFileDir(RuntimeSetup), True, ExitCode) then
  begin
    ReportInstallError('Chạy bộ cài runtime', 'WINDOWS', ExitCode, SysErrorMessage(ExitCode), RuntimeLog);
    Abort;
  end;
  if (ExitCode <> 0) and (ExitCode <> 3010) then
  begin
    ReportInstallError('Cài hoặc giải nén runtime', 'RUNTIME', ExitCode, 'File tải được giữ để thử lại nếu lưu cache thành công.', RuntimeLog);
    Abort;
  end;
  InstallEvent('Runtime cài xong. Mã kết quả: ' + IntToStr(ExitCode));
  ExtractTemporaryFile('online-finalize.py');
  ExtractTemporaryFile('apply-update.py');
  Python := ExpandConstant('{app}\resources\bundle\python\python.exe');
  UpdateLog := InstallLogFolder + '\update-' + InstallSessionId + '.log';
  Parameters := '"' + ExpandConstant('{tmp}\online-finalize.py') + '" --zip "' + ExpandConstant('{tmp}\Editor-Update-Windows-x64.zip') + '" --sha256 {#UpdateHash} --version {#UpdateVersion} --resources "' + ExpandConstant('{app}\resources') + '" --log "' + UpdateLog + '"';
  WizardForm.StatusLabel.Caption := 'Đang áp dụng phiên bản mới nhất…';
  InstallEvent('Bắt đầu áp dụng cập nhật {#UpdateVersion}');
  if not RunInstallStep(Python, Parameters, '', False, ExitCode) then
  begin
    ReportInstallError('Chạy bước cập nhật', 'WINDOWS', ExitCode, SysErrorMessage(ExitCode), UpdateLog);
    Abort;
  end;
  if ExitCode <> 0 then
  begin
    ReportInstallError('Áp dụng bản cập nhật', 'UPDATE', ExitCode, 'Runtime đã được cài. Xem log để biết lỗi kiểm tra gói hoặc ghi file.', UpdateLog);
    Abort;
  end;
  InstallEvent('Đã áp dụng cập nhật');
  ExtractTemporaryFile('COPYING');
  ExtractTemporaryFile('LICENSE.OpenSSL');
  FileCopy(ExpandConstant('{tmp}\COPYING'), ExpandConstant('{app}\resources\app\aria2-COPYING.txt'), False);
  FileCopy(ExpandConstant('{tmp}\LICENSE.OpenSSL'), ExpandConstant('{app}\resources\app\aria2-LICENSE.OpenSSL.txt'), False);
  ExtractTemporaryFile('Lwent-Uninstall-Maintenance.exe');
  WizardForm.StatusLabel.Caption := 'Đang cập nhật bộ gỡ cài đặt…';
  MaintenanceLog := InstallLogFolder + '\uninstaller-' + InstallSessionId + '.log';
  InstallEvent('Bắt đầu cập nhật bộ gỡ cài đặt');
  if not RunInstallStep(ExpandConstant('{tmp}\Lwent-Uninstall-Maintenance.exe'),
    '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="' + ExpandConstant('{app}') + '" /LOG="' + MaintenanceLog + '"',
    '', False, ExitCode) then
  begin
    ReportInstallError('Chạy bước sửa bộ gỡ', 'WINDOWS', ExitCode, SysErrorMessage(ExitCode), MaintenanceLog);
    Abort;
  end;
  if ExitCode <> 0 then
  begin
    ReportInstallError('Cập nhật bộ gỡ cài đặt', 'RUNTIME', ExitCode, 'Ứng dụng đã cập nhật nhưng bộ gỡ chưa cập nhật xong.', MaintenanceLog);
    Abort;
  end;
  DelTree(InstallerCache, True, True, True);
  InstallEvent('Hoàn tất cài đặt {#UpdateVersion}');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssInstall then exit;
  ErrorWasReported := False;
  try
    InstallDownloadedApplication;
  except
    if not ErrorWasReported then
      ReportInstallError('Xử lý bộ cài', 'SETUP', 1, GetExceptionMessage, '');
    Abort;
  end;
end;
