; Append the cleanup policy to the immutable runtime installer's uninstall log.
#ifndef MaintenanceAppId
  #define MaintenanceAppId "{{650AE6AF-6FCB-4612-B136-C0479F0737C7}"
#endif
[Setup]
AppId={#MaintenanceAppId}
AppName=Lwent Editor
AppVersion={#UpdateVersion}
DefaultDirName={localappdata}\Programs\Editor
DefaultGroupName=Editor
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#Output}
OutputBaseFilename=Lwent-Uninstall-Maintenance
SetupIconFile=assets\app-logo.ico
UninstallDisplayIcon={app}\resources\app\assets\app-logo.ico
UninstallLogMode=append
DisableProgramGroupPage=yes
CloseApplications=no

#include "uninstall-policy.iss"
