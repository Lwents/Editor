#define AppName "Editor"
[Setup]
AppId={{650AE6AF-6FCB-4612-B136-C0479F0737C7}
AppName={#AppName}
AppVersion=1.0.0
DefaultDirName={localappdata}\Programs\Editor
DefaultGroupName=Editor
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#Output}
OutputBaseFilename=Editor-Setup-Windows-x64
Compression=lzma2/fast
SolidCompression=yes
DiskSpanning=yes
DiskSliceSize=2000000000
SlicesPerDisk=1
LZMANumBlockThreads=4
WizardStyle=modern
UninstallDisplayIcon={app}\Editor.exe
DisableProgramGroupPage=yes
CloseApplications=no
[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Editor"; Filename: "{app}\Editor.exe"
Name: "{autodesktop}\Editor"; Filename: "{app}\Editor.exe"
[Run]
Filename: "{app}\Editor.exe"; Description: "Mở Editor"; Flags: nowait postinstall skipifsilent
