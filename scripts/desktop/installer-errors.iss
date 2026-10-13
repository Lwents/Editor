[Code]
var InstallLogFolder, InstallSessionLog, InstallSessionId: String;
    ErrorMemo: TNewMemo;
    ErrorWasReported: Boolean;

procedure StartInstallLog;
begin
  InstallSessionId := GetDateTimeString('yyyymmdd-hhnnss', '-', ':');
  InstallLogFolder := ExpandConstant('{localappdata}\LwentEditor\InstallerLogs');
  ForceDirectories(InstallLogFolder);
  InstallSessionLog := InstallLogFolder + '\session-' + InstallSessionId + '.log';
  SaveStringToFile(InstallSessionLog, Utf8Encode('Lwent Editor {#UpdateVersion}' + #13#10), False);
end;

procedure InstallEvent(Text: String);
begin
  Log(Text);
  SaveStringToFile(InstallSessionLog, Utf8Encode(GetDateTimeString('hh:nn:ss', '-', ':') + ' ' + Text + #13#10), True);
end;

function ExplainInstallError(Kind: String; Code: Integer): String;
begin
  Result := 'Chưa xác định được nguyên nhân. Xem log chi tiết hoặc gửi báo cáo lỗi để kiểm tra.';
  if Kind = 'DOWNLOAD' then
    Result := 'Không tải hoặc kiểm tra được dữ liệu. Xem chi tiết dưới đây, kiểm tra kết nối rồi thử lại.';
  if (Kind = 'DOWNLOAD') and (Code = 20) then
    Result := 'File tải về sai SHA256. Bộ cài chưa dùng file này. Chạy lại để kiểm tra và tải lại dữ liệu.';
  if Kind = 'RUNTIME' then
    case Code of
      1: Result := 'Không khởi tạo được bộ cài. Kiểm tra log và quyền truy cập thư mục cài đặt.';
      2: Result := 'Quá trình cài đặt đã bị hủy trước khi bắt đầu.';
      3: Result := 'Lỗi chuẩn bị cài đặt. Có thể thiếu tài nguyên hệ thống; xem log để xác định.';
      4: Result := 'Lỗi trong lúc cài hoặc giải nén dữ liệu. Mã 4 chưa cho biết nguyên nhân cụ thể; xem log chi tiết.';
      5: Result := 'Quá trình cài đặt đã bị hủy hoặc dừng sau một lỗi đọc/ghi file.';
      6: Result := 'Bộ cài bị kết thúc từ bên ngoài.';
      7: Result := 'Chưa thể bắt đầu cài. Đóng app đang sử dụng file và xem log.';
      8: Result := 'Windows cần khởi động lại trước khi có thể cài tiếp.';
    end;
  if Kind = 'WINDOWS' then
    case Code of
      2, 3: Result := 'Không tìm thấy file hoặc thư mục cần thiết. Thử chạy lại bộ cài.';
      5: Result := 'Windows từ chối truy cập. Kiểm tra quyền thư mục và thông báo bảo mật của Windows.';
      32, 33: Result := 'File đang được ứng dụng khác sử dụng. Đóng Lwent Editor rồi thử lại.';
      112: Result := 'Ổ đĩa không còn đủ dung lượng. Dọn dung lượng rồi thử lại.';
      4551: Result := 'Chính sách Application Control của Windows chặn file. Kiểm tra thông báo trong Windows Security.';
    else
      Result := SysErrorMessage(Code);
    end;
  if Kind = 'UPDATE' then
    case Code of
      20: Result := 'Gói cập nhật sai mã kiểm tra SHA256. Chưa áp dụng gói này; tải lại rồi thử lại.';
      21: Result := 'Gói cập nhật thiếu file, sai định dạng hoặc sai phiên bản.';
      22: Result := 'Không có quyền ghi vào thư mục cài đặt hoặc thư mục log.';
      23: Result := 'Không đủ dung lượng để giải nén và sao lưu bản cũ.';
      24: Result := 'File ứng dụng đang bị khóa. Đóng Lwent Editor rồi thử lại.';
      25: Result := 'Đã áp dụng bản cập nhật nhưng chưa sửa được shortcut.';
    end;
end;

procedure OpenInstallerLogs(Sender: TObject);
var Code: Integer;
begin
  if not ShellExec('open', ExpandConstant('{win}\explorer.exe'), AddQuotes(InstallLogFolder), '', SW_SHOWNORMAL, ewNoWait, Code) then
    MsgBox('Không mở được thư mục log: ' + InstallLogFolder, mbError, MB_OK);
end;

procedure CopyInstallerError(Sender: TObject);
begin
  SendMessage(ErrorMemo.Handle, $00B1, 0, -1);
  SendMessage(ErrorMemo.Handle, $0301, 0, 0);
  TNewButton(Sender).Caption := 'Đã sao chép';
end;

procedure ReportInstallError(Step, Kind: String; Code: Integer; Details, DetailLog: String);
var Form: TSetupForm; Button: TNewButton; Text, ReportPath, SetupLogCopy: String;
begin
  ErrorWasReported := True;
  Text := 'Lwent Editor {#UpdateVersion}' + #13#10 +
    'Bước thất bại: ' + Step + #13#10 +
    'Mã lỗi: LW-' + Kind + '-' + IntToStr(Code) + #13#10 +
    ExplainInstallError(Kind, Code) + #13#10 + #13#10 +
    'Chi tiết: ' + Details + #13#10 + #13#10 +
    'Dữ liệu dự án và cấu hình cá nhân không bị xóa bởi bước cài này.' + #13#10 +
    'Đóng app đang chạy, kiểm tra kết nối/dung lượng rồi chạy lại bộ cài.' + #13#10 +
    'Log phiên cài: ' + InstallSessionLog;
  if DetailLog <> '' then Text := Text + #13#10 + 'Log chi tiết: ' + DetailLog;
  SetupLogCopy := InstallLogFolder + '\setup-' + InstallSessionId + '.log';
  if FileExists(ExpandConstant('{log}')) then
    if FileCopy(ExpandConstant('{log}'), SetupLogCopy, False) then
      Text := Text + #13#10 + 'Log bộ cài: ' + SetupLogCopy;
  ReportPath := InstallLogFolder + '\error-' + InstallSessionId + '.txt';
  if SaveStringToFile(ReportPath, Utf8Encode(Text), False) then
    Text := Text + #13#10 + 'Báo cáo lỗi: ' + ReportPath;
  InstallEvent(Text);
  if WizardSilent then exit;
  Form := CreateCustomForm(ScaleX(630), ScaleY(380), False, False);
  try
    Form.Caption := 'Không hoàn tất cài Lwent Editor';
    ErrorMemo := TNewMemo.Create(Form); ErrorMemo.Parent := Form;
    ErrorMemo.SetBounds(ScaleX(16), ScaleY(16), ScaleX(598), ScaleY(306));
    ErrorMemo.ReadOnly := True; ErrorMemo.ScrollBars := ssVertical;
    ErrorMemo.Text := Text;
    Button := TNewButton.Create(Form); Button.Parent := Form;
    Button.SetBounds(ScaleX(16), ScaleY(338), ScaleX(145), ScaleY(28));
    Button.Caption := 'Mở thư mục log'; Button.OnClick := @OpenInstallerLogs;
    Button := TNewButton.Create(Form); Button.Parent := Form;
    Button.SetBounds(ScaleX(174), ScaleY(338), ScaleX(145), ScaleY(28));
    Button.Caption := 'Sao chép báo cáo'; Button.OnClick := @CopyInstallerError;
    Button := TNewButton.Create(Form); Button.Parent := Form;
    Button.SetBounds(ScaleX(508), ScaleY(338), ScaleX(106), ScaleY(28));
    Button.Caption := 'Đóng'; Button.ModalResult := mrOk; Button.Cancel := True;
    Form.ShowModal;
  finally
    ErrorMemo := nil; Form.Free;
  end;
end;
