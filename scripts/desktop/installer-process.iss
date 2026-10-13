[Code]
var InstallStepNumber: Integer;

function JobJsonString(Value: String): String;
begin
  StringChangeEx(Value, '\', '\\', True);
  StringChangeEx(Value, '"', '\"', True);
  StringChangeEx(Value, #13, '\r', True);
  StringChangeEx(Value, #10, '\n', True);
  StringChangeEx(Value, #9, '\t', True);
  Result := '"' + Value + '"';
end;

{ Launch out of process: synchronous Exec disables the wizard while waiting.
  SetProgress pumps the message loop without inventing a percentage. The runtime
  displays its own native /SILENT progress window during decompression. }
function RunInstallStep(FileName, Arguments, Directory: String;
  Visible: Boolean; var ExitCode: Integer): Boolean;
var Page: TOutputProgressWizardPage; Job, JobFile, ResultFile, PidFile,
    Parameters, Base, VisibleJson: String; Line: AnsiString;
    Process: THandle; Code, Pid: Integer; ProcessCode, Started: LongWord;
begin
  Result := False; ExitCode := 1; Process := 0;
  InstallStepNumber := InstallStepNumber + 1;
  Base := ExpandConstant('{tmp}\install-step-') + IntToStr(InstallStepNumber);
  JobFile := Base + '.json'; ResultFile := Base + '.result'; PidFile := Base + '.pid';
  VisibleJson := 'false'; if Visible then VisibleJson := 'true';
  Job := '{"file":' + JobJsonString(FileName) + ',"arguments":' + JobJsonString(Arguments) +
    ',"directory":' + JobJsonString(Directory) + ',"visible":' + VisibleJson +
    ',"result":' + JobJsonString(ResultFile) + ',"pid":' + JobJsonString(PidFile) + '}';
  SaveStringToFile(JobFile, Utf8Encode(Job), False);
  ExtractTemporaryFile('installer-process.ps1');
  Parameters := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{tmp}\installer-process.ps1') + '" -JobFile "' + JobFile + '"';
  Page := CreateOutputProgressPage('Đang cài Lwent Editor', 'Đang xử lý dữ liệu cài đặt.');
  Page.SetText(WizardForm.StatusLabel.Caption, '');
  Page.Show;
  Page.ProgressBar.Style := npbstMarquee;
  Started := DownloadTicks;
  try
    if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      Parameters, '', SW_HIDE, ewNoWait, Code) then
    begin
      ExitCode := Code; exit;
    end;
    { Only the runtime window is visible while it shows real extraction progress. }
    if Visible and not WizardSilent then WizardForm.Hide;
    repeat
      Page.Msg2Label.Caption := 'Đã chạy: ' + IntToStr((DownloadTicks - Started) div 1000) + ' giây';
      Page.SetProgress(0, 100);
      if LoadStringFromFile(ResultFile, Line) then
      begin
        Job := Trim(Utf8Decode(Line));
        if Pos('EXIT|', Job) = 1 then
        begin
          ExitCode := StrToIntDef(Copy(Job, 6, Length(Job)), 1);
          Result := True; break;
        end;
        RaiseException('Không chạy được bước cài đặt: ' + Copy(Job, 7, Length(Job)));
      end;
      if (Process = 0) and LoadStringFromFile(PidFile, Line) then
      begin
        Pid := StrToIntDef(Trim(Line), 0);
        if Pid > 0 then Process := OpenProcess($1000, False, Pid);
      end;
      if Process <> 0 then
      begin
        if GetExitCodeProcess(Process, ProcessCode) and (ProcessCode <> 259) then
        begin
          { Result is written before process exit; allow the next read to see it. }
          if not FileExists(ResultFile) then
            RaiseException('Tiến trình chờ cài đặt kết thúc đột ngột.');
        end;
      end
      else if DownloadTicks - Started > 60000 then
        RaiseException('Không khởi động được bước cài đặt sau 60 giây.');
      Sleep(100);
    until False;
  finally
    if Process <> 0 then CloseHandle(Process);
    Page.Hide;
    if Visible and not WizardSilent then WizardForm.Show;
  end;
end;
