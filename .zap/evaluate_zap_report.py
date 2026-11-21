#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OWASP ZAP DAST Target Validator & Report Evaluator.

Responsibilities:
1. --validate-target <URL>:
   Enforces the safety constraint that DAST scans ONLY target local servers
   running inside CI or developer machines (localhost / 127.0.0.1 / host.docker.internal / ::1).
   Rejects any external or production domain with exit code 1.

2. --report <report_json.json> --summary-out <summary.md> [--rules <.zap/rules.tsv>]:
   Parses ZAP JSON output, applies IGNORE rules from rules.tsv, counts alerts by
   riskcode (3=High, 2=Medium, 1=Low, 0=Informational), writes a Markdown summary
   (and appends to $GITHUB_STEP_SUMMARY when running in GitHub Actions), and
   exits with code 1 ONLY when High risk alerts (riskcode == "3") > 0.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ALLOWED_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "host.docker.internal", "::1"})
SUMMARY_MARKER = "<!-- zap-dast-summary-comment -->"

RISK_META: Dict[str, Tuple[str, str]] = {
    "3": ("High", "🔴"),
    "2": ("Medium", "🟠"),
    "1": ("Low", "🟡"),
    "0": ("Informational", "🔵"),
}


def validate_target_url(url: str) -> Tuple[bool, str]:
    """Validate that target URL uses http/https and strictly points to a local host."""
    if not url or not url.strip():
        return False, "Target URL is empty."

    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        return False, f"Invalid URL scheme '{parsed.scheme}'. Only http/https are allowed."

    hostname = (parsed.hostname or "").lower()
    if hostname not in ALLOWED_LOCAL_HOSTS:
        return (
            False,
            f"SAFETY BLOCK: Refusing to scan non-local host '{hostname}'. "
            f"Only {sorted(ALLOWED_LOCAL_HOSTS)} are permitted.",
        )

    return True, f"Target URL validated (local host '{hostname}'): {url}"


def load_ignored_plugin_ids(rules_path: Optional[Path]) -> set[str]:
    """Load plugin IDs configured with IGNORE from a ZAP rules.tsv file."""
    ignored: set[str] = set()
    if not rules_path or not rules_path.exists():
        return ignored

    for line_no, raw_line in enumerate(rules_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = raw_line.split("\t")
        if len(parts) < 2:
            print(
                f"[WARN] {rules_path}:{line_no} is not tab-separated; ignoring malformed line.",
                file=sys.stderr,
            )
            continue
        plugin_id = parts[0].strip()
        action = parts[1].strip().upper()
        if plugin_id and action == "IGNORE":
            ignored.add(plugin_id)
    return ignored


def collect_alerts(report_data: dict, ignored_ids: set[str]) -> Tuple[List[dict], int]:
    """Extract alerts from ZAP JSON report, filtering out IGNORE plugin IDs."""
    sites_raw = report_data.get("site", [])
    if isinstance(sites_raw, dict):
        sites = [sites_raw]
    elif isinstance(sites_raw, list):
        sites = sites_raw
    else:
        sites = []

    alerts: List[dict] = []
    ignored_count = 0

    for site in sites:
        if not isinstance(site, dict):
            continue
        site_name = str(site.get("@name", ""))
        site_alerts = site.get("alerts", [])
        if not isinstance(site_alerts, list):
            continue
        for alert in site_alerts:
            if not isinstance(alert, dict):
                continue
            plugin_id = str(alert.get("pluginid", "")).strip()
            if plugin_id in ignored_ids:
                ignored_count += 1
                continue

            riskcode = str(alert.get("riskcode", "0")).strip()
            if riskcode not in RISK_META:
                riskcode = "0"

            instances = alert.get("instances", [])
            if not isinstance(instances, list):
                instances = []

            count_raw = alert.get("count", len(instances) or 1)
            try:
                instance_count = int(count_raw)
            except (TypeError, ValueError):
                instance_count = len(instances) or 1

            sample_uri = ""
            if instances and isinstance(instances[0], dict):
                sample_uri = str(instances[0].get("uri", ""))

            alerts.append(
                {
                    "pluginid": plugin_id or "-",
                    "name": str(alert.get("alert") or alert.get("name") or "Unknown Alert"),
                    "riskcode": riskcode,
                    "riskdesc": str(alert.get("riskdesc") or RISK_META[riskcode][0]),
                    "count": instance_count,
                    "cweid": str(alert.get("cweid", "-")),
                    "site": site_name,
                    "sample_uri": sample_uri,
                }
            )

    alerts.sort(key=lambda a: (-int(a["riskcode"]), -a["count"], a["pluginid"]))
    return alerts, ignored_count


def render_summary_markdown(
    alerts: List[dict],
    ignored_count: int,
    target_url: str = "",
    scan_type: str = "baseline",
    project_name: str = "",
) -> Tuple[str, Dict[str, int]]:
    """Build GitHub-flavored Markdown summary and return counts by riskcode."""
    counts = {"3": 0, "2": 0, "1": 0, "0": 0}
    for alert in alerts:
        counts[alert["riskcode"]] = counts.get(alert["riskcode"], 0) + 1

    high_count = counts["3"]
    status_badge = "FAIL (High Risk Detected)" if high_count > 0 else "PASS (No High Risk Detected)"
    title_prefix = f"[{project_name}] " if project_name else ""

    lines = [
        SUMMARY_MARKER,
        f"## {title_prefix}OWASP ZAP DAST Security Scan Summary",
        "",
        f"- **Status**: **{status_badge}**",
        f"- **Scan Type**: `{scan_type}`",
    ]
    if target_url:
        lines.append(f"- **Target URL**: `{target_url}`")
    lines.extend(
        [
            f"- **Policy**: CI fails only on **High** (`riskcode == 3`) findings; Medium / Low / Informational are warnings.",
            f"- **Suppressed (`IGNORE` rules)**: {ignored_count} alert rule(s)",
            "",
            "| Risk Level | Alerts | Policy Action |",
            "| :--- | :---: | :--- |",
            f"| 🔴 High | **{counts['3']}** | Fail CI (`exit 1`) |",
            f"| 🟠 Medium | {counts['2']} | Warn only |",
            f"| 🟡 Low | {counts['1']} | Warn only |",
            f"| 🔵 Informational | {counts['0']} | Info only |",
            "",
        ]
    )

    if alerts:
        lines.extend(
            [
                "### Detected Alerts",
                "",
                "| Risk | Plugin ID | Alert | Instances | CWE | Sample URI |",
                "| :---: | :---: | :--- | :---: | :---: | :--- |",
            ]
        )
        for alert in alerts[:50]:
            label, icon = RISK_META[alert["riskcode"]]
            name_safe = alert["name"].replace("|", "\\|").replace("\n", " ")
            uri_safe = (alert["sample_uri"] or alert["site"] or "-").replace("|", "\\|")
            if len(uri_safe) > 80:
                uri_safe = uri_safe[:77] + "..."
            lines.append(
                f"| {icon} {label} | `{alert['pluginid']}` | {name_safe} | {alert['count']} | {alert['cweid']} | `{uri_safe}` |"
            )
        if len(alerts) > 50:
            lines.append(f"\n*...and {len(alerts) - 50} more alert(s) in the full HTML/JSON artifact.*")
    else:
        lines.append("No active alerts remained after applying `.zap/rules.tsv`.")

    lines.append("")
    lines.append("> Full reports (`report_html.html`, `report_json.json`, `report_md.md`) are attached as CI workflow artifacts.")
    return "\n".join(lines) + "\n", counts


def evaluate_report(
    report_path: Path,
    summary_out: Path,
    rules_path: Optional[Path] = None,
    target_url: str = "",
    scan_type: str = "baseline",
    project_name: str = "",
) -> int:
    """Parse report JSON, write Markdown summary & Step Summary, and return exit code."""
    if not report_path.exists():
        msg = (
            f"{SUMMARY_MARKER}\n"
            f"## OWASP ZAP DAST Security Scan Summary\n\n"
            f"- **Status**: **ERROR (Report JSON missing)**\n"
            f"- Expected report file not found: `{report_path}`\n"
        )
        summary_out.parent.mkdir(parents=True, exist_ok=True)
        summary_out.write_text(msg, encoding="utf-8")
        print(f"[ERROR] ZAP report JSON not found: {report_path}", file=sys.stderr)
        return 1

    try:
        report_data = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[ERROR] Failed to parse ZAP JSON report '{report_path}': {exc}", file=sys.stderr)
        return 1

    ignored_ids = load_ignored_plugin_ids(rules_path)
    alerts, ignored_count = collect_alerts(report_data, ignored_ids)
    summary_md, counts = render_summary_markdown(
        alerts=alerts,
        ignored_count=ignored_count,
        target_url=target_url,
        scan_type=scan_type,
        project_name=project_name,
    )

    summary_out.parent.mkdir(parents=True, exist_ok=True)
    summary_out.write_text(summary_md, encoding="utf-8")

    step_summary_env = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary_env:
        try:
            with open(step_summary_env, "a", encoding="utf-8") as f:
                f.write(summary_md + "\n")
        except OSError as exc:
            print(f"[WARN] Could not write GITHUB_STEP_SUMMARY: {exc}", file=sys.stderr)

    print(
        f"[ZAP Summary] High={counts['3']}, Medium={counts['2']}, "
        f"Low={counts['1']}, Info={counts['0']}, Ignored={ignored_count}"
    )

    if counts["3"] > 0:
        print(
            f"[FAIL] {counts['3']} High-risk vulnerability alert(s) detected. Failing CI gate.",
            file=sys.stderr,
        )
        return 1

    if counts["2"] > 0 or counts["1"] > 0:
        print("[WARN] Medium/Low alerts detected (reporting only; CI gate passed).")
    else:
        print("[PASS] No High/Medium/Low alerts detected.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="OWASP ZAP DAST target validator & report evaluator")
    parser.add_argument("--validate-target", metavar="URL", help="Validate that URL targets only a local server")
    parser.add_argument("--report", metavar="JSON_PATH", help="Path to ZAP report_json.json")
    parser.add_argument("--summary-out", metavar="MD_PATH", help="Output path for Markdown summary")
    parser.add_argument("--rules", metavar="TSV_PATH", default=".zap/rules.tsv", help="Path to rules.tsv")
    parser.add_argument("--target-url", default="", help="Scanned target URL for summary metadata")
    parser.add_argument("--scan-type", default="baseline", help="Scan type (baseline or full)")
    parser.add_argument("--project-name", default="", help="Project display name")
    args = parser.parse_args()

    if args.validate_target is not None:
        ok, message = validate_target_url(args.validate_target)
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if args.report:
        summary_out = Path(args.summary_out) if args.summary_out else Path(args.report).with_name("summary.md")
        rules_path = Path(args.rules) if args.rules else None
        return evaluate_report(
            report_path=Path(args.report),
            summary_out=summary_out,
            rules_path=rules_path,
            target_url=args.target_url,
            scan_type=args.scan_type,
            project_name=args.project_name,
        )

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
