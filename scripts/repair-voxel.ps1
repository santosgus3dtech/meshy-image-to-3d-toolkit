$ErrorActionPreference = "Stop"

$blender = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
if (-not (Test-Path -LiteralPath $blender)) {
  $command = Get-Command blender -ErrorAction SilentlyContinue
  if (-not $command) {
    throw "Blender não encontrado. Adicione o Blender ao PATH ou ajuste scripts/repair-voxel.ps1."
  }
  $blender = $command.Source
}

& $blender -b --python "src\blender_voxel_repair.py" -- @args
exit $LASTEXITCODE
