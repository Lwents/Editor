param([string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if (-not $OutputDirectory) { $OutputDirectory = $root }
New-Item -ItemType Directory -Force $OutputDirectory | Out-Null
$template = Get-Content (Join-Path $PSScriptRoot 'Launcher.cs') -Raw
foreach ($item in @(
    @{ Class = 'EditorLauncher'; Action = 'start'; File = 'Editor.exe' },
    @{ Class = 'EditorStopper'; Action = 'stop'; File = 'Dung_Editor.exe' }
)) {
    $source = $template.Replace('@CLASS@', $item.Class).Replace('@ACTION@', $item.Action)
    $temporary = Join-Path $OutputDirectory ([guid]::NewGuid().ToString('N') + '.exe')
    try {
        Add-Type -TypeDefinition $source -Language CSharp -ReferencedAssemblies 'System.dll','System.Windows.Forms.dll' -OutputAssembly $temporary -OutputType WindowsApplication
        Move-Item $temporary (Join-Path $OutputDirectory $item.File) -Force
    } finally {
        if (Test-Path $temporary) { Remove-Item $temporary -Force }
    }
}
