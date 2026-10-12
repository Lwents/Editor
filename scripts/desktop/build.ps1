param([string]$BuildRoot=(Join-Path $env:LOCALAPPDATA 'EditorBuild'),[switch]$Prepared)
$ErrorActionPreference='Stop'
$Repo=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
# Prepare a separate copy so a production build never reads personal .env files.
if(-not $Prepared){& "$PSScriptRoot\prepare.ps1" -BuildRoot $BuildRoot}
# Stage never copies personal .env files, storage, credentials or router data.
& "$PSScriptRoot\stage.ps1" -BuildRoot $BuildRoot
# Staging checks robocopy's success codes (0 through 7) and throws on failure.
$Output=Join-Path $Repo 'release'
$InstallerOutput=Join-Path $BuildRoot 'installer'
New-Item -ItemType Directory -Force $InstallerOutput|Out-Null
New-Item -ItemType Directory -Force $Output|Out-Null
& "$BuildRoot\tools\inno\ISCC.exe" /Qp "/DPayload=$BuildRoot\app" "/DOutput=$InstallerOutput" "$PSScriptRoot\installer.iss"
if($LASTEXITCODE -ne 0){throw 'Installer build failed'}
& "$BuildRoot\python\python.exe" "$PSScriptRoot\package-release.py" $InstallerOutput $Output
if($LASTEXITCODE -ne 0){throw 'Installer archive failed'}
Write-Output "Installer ready: $Output\Editor-Windows-x64.zip"
