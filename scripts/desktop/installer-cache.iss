[Code]
function CreateHardLink(NewName, ExistingName: String; Reserved: Integer): Boolean;
  external 'CreateHardLinkW@kernel32.dll stdcall';

function InstallerCache: String;
begin
  Result := ExpandConstant('{localappdata}\LwentEditor\InstallerCache');
end;

procedure RestoreRuntimeFile(Name, Hash: String);
var Cached, Download: String;
begin
  Cached := InstallerCache + '\' + Name;
  Download := ExpandConstant('{tmp}\') + Name;
  if FileExists(Download) then exit;
  if not FileExists(Cached) then exit;
  if GetSHA256OfFile(Cached) <> Hash then
  begin
    DeleteFile(Cached);
    exit;
  end;
  if not CreateHardLink(Download, Cached, 0) then
    FileCopy(Cached, Download, False);
end;

procedure PreserveRuntimeFile(Name, Hash: String);
var Cached, Download: String;
begin
  Cached := InstallerCache + '\' + Name;
  Download := ExpandConstant('{tmp}\') + Name;
  if FileExists(Cached) then
  begin
    if GetSHA256OfFile(Cached) = Hash then exit;
    DeleteFile(Cached);
  end;
  if not FileExists(Download) then exit;
  if GetSHA256OfFile(Download) <> Hash then exit;
  ForceDirectories(InstallerCache);
  if not CreateHardLink(Cached, Download, 0) then
    if not FileCopy(Download, Cached, False) then
      Log('Could not preserve installer file for retry: ' + Name);
end;
