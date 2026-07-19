$ErrorActionPreference = 'Stop'

$workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $workspace

$python311 = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
if (-not (Test-Path $python311)) {
    Write-Host "Python 3.11 not found at $python311"
    Write-Host "Please install it first with: winget install --id Python.Python.3.11 -e"
    exit 1
}

$venvPath = Join-Path $workspace '.venv311'
if (-not (Test-Path $venvPath)) {
    & $python311 -m venv $venvPath
}

$activateScript = Join-Path $venvPath 'Scripts\Activate.ps1'
. $activateScript

python -m pip install --upgrade pip setuptools wheel
python -m pip install openvino openvino-dev nncf optimum-intel transformers faster-whisper

Write-Host ''
Write-Host 'Intel NPU setup completed.'
Write-Host 'Verify with:'
Write-Host '  python -c "from faster_whisper import WhisperModel; print(\"ok\")"'
Write-Host '  python -c "import openvino; print(openvino.__version__)"'
