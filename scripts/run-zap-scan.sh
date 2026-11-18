#!/usr/bin/env bash
set -euo pipefail

# Local OWASP ZAP DAST Runner (Template)
# Usage:
#   ./scripts/run-zap-scan.sh [baseline|full] [TARGET_URL]
# Example:
#   ./scripts/run-zap-scan.sh baseline http://127.0.0.1:8005/

SCAN_MODE="${1:-baseline}"
DEFAULT_TARGET_URL="${ZAP_TARGET_URL:-http://127.0.0.1:8005/}"
TARGET_URL="${2:-$DEFAULT_TARGET_URL}"
PROJECT_NAME="${ZAP_PROJECT_NAME:-SummarizePodcast}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPORT_DIR="${REPO_ROOT}/.zap/reports"
RULES_FILE="${REPO_ROOT}/.zap/rules.tsv"
EVAL_SCRIPT="${REPO_ROOT}/.zap/evaluate_zap_report.py"

if [[ "${SCAN_MODE}" == "full" ]]; then
  ZAP_SCRIPT="zap-full-scan.py"
elif [[ "${SCAN_MODE}" == "baseline" ]]; then
  ZAP_SCRIPT="zap-baseline.py"
else
  echo "[ERROR] Invalid scan mode '${SCAN_MODE}'. Use 'baseline' or 'full'." >&2
  exit 1
fi

PYTHON_CMD=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD="python"
else
  echo "[ERROR] Python 3 is required to validate target URL and evaluate ZAP reports." >&2
  exit 1
fi

# 1. Safety guard: allow local targets ONLY
"${PYTHON_CMD}" "${EVAL_SCRIPT}" --validate-target "${TARGET_URL}"

# Resolve container-accessible URL for Docker Desktop (macOS/Windows) vs Linux
DOCKER_NET_ARGS=(--add-host=host.docker.internal:host-gateway)
CONTAINER_TARGET_URL="${TARGET_URL}"
UNAME_S="$(uname -s 2>/dev/null || echo Unknown)"
if [[ "${UNAME_S}" == "Linux" ]]; then
  DOCKER_NET_ARGS=(--network host)
else
  CONTAINER_TARGET_URL="${CONTAINER_TARGET_URL/http:\/\/127.0.0.1/http:\/\/host.docker.internal}"
  CONTAINER_TARGET_URL="${CONTAINER_TARGET_URL/http:\/\/localhost/http:\/\/host.docker.internal}"
fi

# 2. Verify Docker is installed and daemon is running
if ! command -v docker >/dev/null 2>&1; then
  echo "[ERROR] 'docker' command was not found. Please install Docker Desktop / Docker Engine first." >&2
  echo "Once Docker is installed and the local app server is running at ${TARGET_URL}, run:" >&2
  echo "  MSYS_NO_PATHCONV=1 docker run --rm --add-host=host.docker.internal:host-gateway -v \"${REPORT_DIR}:/zap/wrk/:rw\" ghcr.io/zaproxy/zaproxy:stable ${ZAP_SCRIPT} -t \"${CONTAINER_TARGET_URL}\" -c rules.tsv -J report_json.json -w report_md.md -r report_html.html -I" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "[ERROR] Docker daemon is not running. Start Docker Desktop / Docker service and retry." >&2
  exit 1
fi

# 3. Prepare report directory and rules copy inside /zap/wrk
mkdir -p "${REPORT_DIR}"
chmod 777 "${REPORT_DIR}" 2>/dev/null || true
trap 'rm -f "${REPORT_DIR}/rules.tsv"' EXIT
cp "${RULES_FILE}" "${REPORT_DIR}/rules.tsv"

echo "[*] Running OWASP ZAP (${ZAP_SCRIPT}) against ${CONTAINER_TARGET_URL}..."
MSYS_NO_PATHCONV=1 docker run --rm \
  "${DOCKER_NET_ARGS[@]}" \
  -v "${REPORT_DIR}:/zap/wrk/:rw" \
  ghcr.io/zaproxy/zaproxy:stable \
  "${ZAP_SCRIPT}" \
  -t "${CONTAINER_TARGET_URL}" \
  -c rules.tsv \
  -J report_json.json \
  -w report_md.md \
  -r report_html.html \
  -I

rm -f "${REPORT_DIR}/rules.tsv"

# 4. Evaluate JSON report (exit 1 ONLY on High risk)
"${PYTHON_CMD}" "${EVAL_SCRIPT}" \
  --report "${REPORT_DIR}/report_json.json" \
  --summary-out "${REPORT_DIR}/summary.md" \
  --rules "${RULES_FILE}" \
  --target-url "${TARGET_URL}" \
  --scan-type "${SCAN_MODE}" \
  --project-name "${PROJECT_NAME}"
