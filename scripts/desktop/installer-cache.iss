[Code]
function CreateHardLink(NewName, ExistingName: String; Reserved: Integer): Boolean;
  external 'CreateHardLinkW@kernel32.dll stdcall';

function InstallerCache: String;
begin
  Result := ExpandConstant('{localappdata}\LwentEditor\InstallerCache');
end;
