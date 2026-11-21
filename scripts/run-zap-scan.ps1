<#
.SYNOPSIS
    Local OWASP ZAP DAST Runner for Windows PowerShell (Template)
.EXAMPLE
    .\scripts\run-zap-scan.ps1 -ScanMode baseline -TargetUrl http://127.0.0.1:8005/
#>
param(
    [ValidateSet("baseline", "full")]
    [string]$ScanMode = "baseline",
    [string]$TargetUrl = "http://127.0.0.1:8005/",
    [string]$ProjectName = "SummarizePodcast"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$ReportDir = Join-Path $RepoRoot ".zap\reports"
$RulesFile = Join-Path $RepoRoot ".zap\rules.tsv"
$EvalScript = Join-Path $RepoRoot ".zap\evaluate_zap_report.py"

$ZapScript = if ($ScanMode -eq "full") { "zap-full-scan.py" } else { "zap-baseline.py" }

$PythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $PythonCmd = $cmd
        break
    }
}
if (-not $PythonCmd) {
    Write-Error "[ERROR] Python 3 is required to validate target URL and evaluate ZAP reports."
    exit 1
}

# 1. Safety guard: allow local targets ONLY
& $PythonCmd $EvalScript --validate-target $TargetUrl
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

# Rewrite loopback host to host.docker.internal for Windows Docker Desktop containers
$ContainerTargetUrl = $TargetUrl -replace '^http://127\.0\.0\.1', 'http://host.docker.internal' `
                                 -replace '^http://localhost', 'http://host.docker.internal'

# 2. Verify Docker CLI & daemon
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] 'docker' command was not found. Install Docker Desktop (https://www.docker.com/products/docker-desktop/) and ensure 'docker' is in PATH." -ForegroundColor Red
    Write-Host "After starting Docker Desktop and your local app server at $TargetUrl, run:" -ForegroundColor Yellow
    Write-Host "  docker run --rm --add-host=host.docker.internal:host-gateway -v `"${ReportDir}:/zap/wrk/:rw`" ghcr.io/zaproxy/zaproxy:stable $ZapScript -t `"$ContainerTargetUrl`" -c rules.tsv -J report_json.json -w report_md.md -r report_html.html -I"
    exit 1
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Docker daemon is not running. Start Docker Desktop and retry." -ForegroundColor Red
    exit 1
}

# 3. Prepare report directory and rules copy
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
Copy-Item -Path $RulesFile -Destination (Join-Path $ReportDir "rules.tsv") -Force

Write-Host "[*] Running OWASP ZAP ($ZapScript) against $ContainerTargetUrl..."
docker run --rm `
    --add-host=host.docker.internal:host-gateway `
    -v "${ReportDir}:/zap/wrk/:rw" `
    ghcr.io/zaproxy/zaproxy:stable `
    $ZapScript `
    -t $ContainerTargetUrl `
    -c rules.tsv `
    -J report_json.json `
    -w report_md.md `
    -r report_html.html `
    -I
$DockerExit = $LASTEXITCODE

Remove-Item -Path (Join-Path $ReportDir "rules.tsv") -Force -ErrorAction SilentlyContinue

if ($DockerExit -ne 0 -and -not (Test-Path (Join-Path $ReportDir "report_json.json"))) {
    Write-Error "[ERROR] ZAP container exited with code $DockerExit without producing report_json.json."
    exit $DockerExit
}

# 4. Evaluate JSON report (exit 1 ONLY on High risk)
& $PythonCmd $EvalScript `
    --report (Join-Path $ReportDir "report_json.json") `
    --summary-out (Join-Path $ReportDir "summary.md") `
    --rules $RulesFile `
    --target-url $TargetUrl `
    --scan-type $ScanMode `
    --project-name $ProjectName
exit $LASTEXITCODE
