param([string]$BuildRoot=(Join-Path $env:LOCALAPPDATA 'EditorBuild'))
$ErrorActionPreference='Stop'
$Source="$BuildRoot\fe\apps\web\.next\standalone"
$Target="$BuildRoot\app\resources\bundle\frontend"
$Backup="$BuildRoot\frontend-previous"
if(Test-Path $Backup){Remove-Item $Backup -Recurse -Force}
if(Test-Path $Target){Move-Item $Target $Backup}
New-Item -ItemType Directory -Force $Target|Out-Null
& robocopy.exe $Source $Target /E /XD node_modules /XF '.env*' '*.map' /NFL /NDL /NJH /NJS /NP|Out-Null
if($LASTEXITCODE -ge 8){throw 'Standalone copy failed'}
# Bun's trace contains absolute junctions pointing to the development install.
# Copy only the real traced packages and normalize them to ordinary node_modules.
foreach($Entry in (Get-ChildItem "$Source\node_modules\.bun" -Directory|Where-Object {$_.Name -ne 'node_modules'})){
 if($Entry.Name -like 'next@*' -and $Entry.Name -notlike 'next@16.1.3+*'){continue}
 foreach($Dir in (Get-ChildItem "$($Entry.FullName)\node_modules" -Directory)){
  $Candidates=if($Dir.Name.StartsWith('@')){Get-ChildItem $Dir.FullName -Directory}else{@($Dir)}
  foreach($Package in $Candidates){
   if($Package.Attributes -band [System.IO.FileAttributes]::ReparsePoint){continue}
   $Metadata=Join-Path $Package.FullName 'package.json'
   if(-not(Test-Path $Metadata)){continue}
   $Name=(Get-Content $Metadata -Raw|ConvertFrom-Json).name
   if(-not $Name -or $Name -match '\.\.|\\'){throw 'Invalid traced package name'}
   $Dest=Join-Path "$Target\node_modules" $Name
   New-Item -ItemType Directory -Force $Dest|Out-Null
   & robocopy.exe $Package.FullName $Dest /E /XD node_modules /XF '*.map' '.env*' /NFL /NDL /NJH /NJS /NP|Out-Null
   if($LASTEXITCODE -ge 8){throw "Traced package copy failed: $Name"}
  }
 }
}
foreach($Pair in @(@("$BuildRoot\fe\apps\web\.next\static","$Target\apps\web\.next\static"),@("$BuildRoot\fe\apps\web\public","$Target\apps\web\public"))){
 & robocopy.exe $Pair[0] $Pair[1] /E /XF '*.map' '.env*' /NFL /NDL /NJH /NJS /NP|Out-Null
 if($LASTEXITCODE -ge 8){throw 'Frontend assets copy failed'}
}
Write-Output 'Standalone frontend normalized'
