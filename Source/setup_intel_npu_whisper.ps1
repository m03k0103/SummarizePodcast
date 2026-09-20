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

python -m pip install pip==24.2 setuptools==75.1.0 wheel==0.44.0
python -m pip install openvino==2024.4.0 openvino-dev==2024.4.0 nncf==2.13.0 optimum-intel==1.20.0 transformers==4.45.1 faster-whisper==1.0.3

Write-Host ''
Write-Host 'Intel NPU setup completed.'
Write-Host 'Verify with:'
Write-Host '  python -c "from faster_whisper import WhisperModel; print(\"ok\")"'
Write-Host '  python -c "import openvino; print(openvino.__version__)"'
