[Code]
var FastDownloadPage: TOutputProgressWizardPage;
    StopDownloadButton: TNewButton;
    DownloadCancelled: Boolean;
    DownloadFailure: String;
    DownloadPercent: Integer;

function OpenProcess(Access: LongWord; Inherit: Boolean; ProcessId: LongWord): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function GetExitCodeProcess(Process: THandle; var Code: LongWord): Boolean;
  external 'GetExitCodeProcess@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';
function DownloadTicks: LongWord;
  external 'GetTickCount@kernel32.dll stdcall';

procedure StopFastDownload(Sender: TObject);
begin
  DownloadCancelled := True;
  StopDownloadButton.Enabled := False;
  StopDownloadButton.Caption := 'Đang dừng…';
  SaveStringToFile(ExpandConstant('{tmp}\download.cancel'), 'cancel', False);
end;

procedure DownloadOutput(const S: String; const Error, FirstLine: Boolean);
var Fields: TStringList; Percent: Integer; Phase: String;
begin
  if Pos('FAIL|', S) = 1 then
  begin
    DownloadFailure := Copy(S, 6, Length(S));
    InstallEvent(DownloadFailure);
  end;
  if Pos('PROGRESS|', S) <> 1 then exit;
  Fields := TStringList.Create;
  try
    Fields.Delimiter := '|'; Fields.StrictDelimiter := True;
    Fields.DelimitedText := S;
    if Fields.Count <> 8 then exit;
    Percent := StrToIntDef(Fields[1], 0);
    DownloadPercent := Percent;
    Phase := 'Đang tải dữ liệu';
    if Fields[7] = 'verify' then Phase := 'Đang tải / kiểm tra SHA256';
    if Fields[7] = 'complete' then Phase := 'Đã tải và kiểm tra xong';
    FastDownloadPage.SetText(Phase + ' — ' + Fields[1] + '%', '');
    FastDownloadPage.Msg2Label.Caption := Fields[3] + ' | ' + Fields[2] + ' MiB/s | Còn khoảng: ' + Fields[4] + #13#10 +
      Fields[5] + ' file đang xử lý | ' + Fields[6] + ' kết nối';
    FastDownloadPage.SetProgress(Percent, 100);

  finally
    Fields.Free;
  end;
end;

function DownloadInstallation: Boolean;
var Code, Pid: Integer; Parameters, LogPath, UpdateSource, UpdateTarget: String;
    StatusFile, ResultFile, PidFile: String; Line: AnsiString;
    Process: THandle; ProcessCode, Started: LongWord;
begin
  Result := False;
  DownloadCancelled := False; DownloadFailure := ''; DownloadPercent := 0;
  StatusFile := ExpandConstant('{tmp}\download.status');
  ResultFile := ExpandConstant('{tmp}\download.result');
  PidFile := ExpandConstant('{tmp}\download.pid');
  DeleteFile(StatusFile); DeleteFile(ResultFile); DeleteFile(PidFile);
  Process := 0; Pid := 0;
  DeleteFile(ExpandConstant('{tmp}\download.cancel'));
  ExtractTemporaryFile('aria2c.exe');
  ExtractTemporaryFile('online-download.ps1');
  ExtractTemporaryFile('installation-downloads.json');
  LogPath := InstallLogFolder + '\download-' + InstallSessionId + '.log';
  Parameters := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{tmp}\online-download.ps1') + '" -Manifest "' +
    ExpandConstant('{tmp}\installation-downloads.json') + '" -Cache "' + InstallerCache +
    '" -Aria "' + ExpandConstant('{tmp}\aria2c.exe') + '" -CancelFile "' +
    ExpandConstant('{tmp}\download.cancel') + '" -LogFile "' + LogPath +
    '" -ParentId ' + IntToStr(GetCurrentProcessId) +
    ' -StatusFile "' + StatusFile + '" -ResultFile "' + ResultFile + '" -PidFile "' + PidFile + '"';
  FastDownloadPage.SetText('Chuẩn bị tải / tiếp tục dữ liệu đã tải…', 'Tối đa 2 file cùng lúc, 4 kết nối mỗi file.');
  FastDownloadPage.SetProgress(0, 100);
  StopDownloadButton.Enabled := True; StopDownloadButton.Caption := 'Dừng tải';
  FastDownloadPage.Show;
  FastDownloadPage.Msg2Label.Visible := True;
  FastDownloadPage.SetProgress(0, 100);
  try
    if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      Parameters, '', SW_HIDE, ewNoWait, Code) then
    begin
      ReportInstallError('Mở trình tải dữ liệu', 'WINDOWS', Code, SysErrorMessage(Code), LogPath);
      exit;
    end;
    Started := DownloadTicks;
    repeat
      if LoadStringFromFile(StatusFile, Line) then DownloadOutput(Trim(Utf8Decode(Line)), False, False);
      FastDownloadPage.SetProgress(DownloadPercent, 100);
      Code := -1;
      if LoadStringFromFile(ResultFile, Line) then Code := StrToIntDef(Trim(Line), -1);
      if Code >= 0 then break;
      if (Process = 0) and LoadStringFromFile(PidFile, Line) then
      begin
        Pid := StrToIntDef(Trim(Line), 0);
        if Pid > 0 then Process := OpenProcess($1000, False, Pid);
      end;
      if Process <> 0 then
      begin
        if GetExitCodeProcess(Process, ProcessCode) and (ProcessCode <> 259) then
          RaiseException('Trình tải kết thúc đột ngột. Xem log: ' + LogPath);
      end
      else if DownloadTicks - Started > 60000 then
        RaiseException('Không khởi động được trình tải sau 60 giây. Xem log: ' + LogPath);
      Sleep(200);
    until False;
    if DownloadCancelled or (Code = 2) then
    begin
      InstallEvent('Đã dừng tải; giữ dữ liệu tải dở để tiếp tục');
      exit;
    end;
    if Code <> 0 then
    begin
      ReportInstallError('Tải hoặc kiểm tra dữ liệu', 'DOWNLOAD', Code, DownloadFailure, LogPath);
      exit;
    end;
    UpdateSource := InstallerCache + '\{#UpdateArchive}';
    UpdateTarget := ExpandConstant('{tmp}\Editor-Update-Windows-x64.zip');
    DeleteFile(UpdateTarget);
    if not CreateHardLink(UpdateTarget, UpdateSource, 0) then
      if not FileCopy(UpdateSource, UpdateTarget, False) then
        RaiseException('Không tạo được file cập nhật từ cache tải xuống.');
    Result := True;
  finally
    if not Result then SaveStringToFile(ExpandConstant('{tmp}\download.cancel'), 'cancel', False);
    if Process <> 0 then CloseHandle(Process);
    FastDownloadPage.Hide;
  end;
end;

