; Only application-owned folders are removed recursively. Never wildcard {app}.
#ifndef DesktopDataName
  #define DesktopDataName "editor-desktop"
#endif
#ifndef GeminiDataName
  #define GeminiDataName "LwentEditor"
#endif
[UninstallDelete]
Type: filesandordirs; Name: "{app}\resources"
Type: filesandordirs; Name: "{app}\locales"
Type: filesandordirs; Name: "{userappdata}\{#DesktopDataName}\updates"
Type: filesandordirs; Name: "{localappdata}\{#GeminiDataName}\InstallerCache"
Type: filesandordirs; Name: "{localappdata}\{#GeminiDataName}\InstallerLogs"
Type: dirifempty; Name: "{localappdata}\{#GeminiDataName}"
Type: dirifempty; Name: "{app}"

[Code]
var RemovePersonalData: Boolean;

function InitializeUninstall: Boolean;
var Answer, ExitCode: Integer; Command, InstallRoot, Uninstaller: String;
begin
  Result := False;
  RemovePersonalData := False;
  // Check only processes running from this installation, without killing them.
  InstallRoot := ExpandConstant('{app}');
  StringChangeEx(InstallRoot, '''', '''''', True);
  Uninstaller := ExpandConstant('{uninstallexe}');
  StringChangeEx(Uninstaller, '''', '''''', True);
  Command := '-NoProfile -NonInteractive -Command "' +
    '$root=[IO.Path]::GetFullPath(''' +
    InstallRoot +
    ''').TrimEnd([char]92)+[char]92; ' +
    '$uninstaller=''' + Uninstaller + '''; ' +
    '$active=Get-Process | Where-Object { $_.Path -and $_.Path -ne $uninstaller -and $_.Path.StartsWith($root,[StringComparison]::OrdinalIgnoreCase) }; ' +
    'if($active){exit 1}else{exit 0}"';
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    Command, '', SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then
  begin
    if not UninstallSilent then
      MsgBox('Hãy đóng Lwent Editor trước khi gỡ cài đặt rồi thử lại.', mbError, MB_OK);
    exit;
  end;
  if not UninstallSilent then
  begin
    Answer := MsgBox('Bạn có muốn xóa cả dữ liệu cá nhân?' + #13#10 + #13#10 +
      'Có: xóa dự án lưu trong app, cấu hình API và phiên đăng nhập Gemini. Không thể hoàn tác.' + #13#10 +
      'Không: chỉ gỡ ứng dụng, model và cache cập nhật; giữ dữ liệu cá nhân.' + #13#10 +
      'Các video hoặc file đã xuất ở thư mục khác được giữ nguyên.' + #13#10 + #13#10 +
      'Nếu chọn Có, hãy đóng cửa sổ Gemini riêng của app trước khi tiếp tục.',
      mbConfirmation, MB_YESNOCANCEL or MB_DEFBUTTON2);
    if Answer = IDCANCEL then exit;
    RemovePersonalData := Answer = IDYES;
  end;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var DesktopData, GeminiData: String; CleanDesktop, CleanGemini: Boolean;
begin
  if (CurUninstallStep <> usPostUninstall) or not RemovePersonalData then exit;
  DesktopData := ExpandConstant('{userappdata}\{#DesktopDataName}');
  GeminiData := ExpandConstant('{localappdata}\{#GeminiDataName}\GeminiWeb');
  CleanDesktop := not DirExists(DesktopData);
  if not CleanDesktop then CleanDesktop := DelTree(DesktopData, True, True, True);
  CleanGemini := not DirExists(GeminiData);
  if not CleanGemini then CleanGemini := DelTree(GeminiData, True, True, True);
  RemoveDir(ExpandConstant('{localappdata}\{#GeminiDataName}'));
  if not (CleanDesktop and CleanGemini) then
    MsgBox('Đã gỡ ứng dụng nhưng còn dữ liệu đang được sử dụng. Đóng cửa sổ Gemini và dọn các thư mục còn lại:' +
      '' + #13#10 + DesktopData + #13#10 + GeminiData, mbError, MB_OK);
end;
