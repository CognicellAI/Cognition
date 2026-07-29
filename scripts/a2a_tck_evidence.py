"""Build permanent, self-explaining A2A TCK release evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REQUIRED_REPORTS = (
    "compatibility.json",
    "compatibility.html",
    "tck_report.html",
    "junitreport.xml",
)
_AUTHORIZATION_PATTERN = re.compile(
    r"(?im)^(\s*authorization\s*[:=]\s*).+$",
)
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


def build_evidence(
    *,
    reports_dir: Path,
    fixture_log: Path,
    output_dir: Path,
    cognition_version: str,
    cognition_sha: str,
    tck_sha: str,
    command: str,
    suite: str,
    workflow_url: str,
    tck_exit_code: int,
    generated_at: str | None = None,
) -> tuple[Path, Path, bool]:
    """Create a release evidence archive and return its path, checksum, and gate."""
    compatibility_path = reports_dir / "compatibility.json"
    missing = [name for name in REQUIRED_REPORTS if not (reports_dir / name).is_file()]
    if missing:
        names = ", ".join(missing)
        raise ValueError(f"Missing required A2A TCK reports: {names}")

    try:
        compatibility = json.loads(compatibility_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError("A2A TCK compatibility.json is unreadable or malformed") from exc
    if not isinstance(compatibility, dict):
        raise ValueError("A2A TCK compatibility.json must contain an object")

    requirements = _requirements(compatibility)
    counts = _requirement_counts(requirements)
    must_failures = sorted(
        requirement_id
        for requirement_id, result in requirements.items()
        if result.get("level") == "MUST" and result.get("status") == "FAIL"
    )
    gate_passed = tck_exit_code == 0 and not must_failures
    timestamp = generated_at or datetime.now(UTC).isoformat()
    version_label = cognition_version if cognition_version.startswith("v") else f"v{cognition_version}"
    asset_stem = f"cognition-{version_label}-a2a-v1-tck"
    bundle_dir = output_dir / asset_stem
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    for name in REQUIRED_REPORTS:
        shutil.copy2(reports_dir / name, bundle_dir / name)
    fixture_log_text = fixture_log.read_text(encoding="utf-8") if fixture_log.is_file() else ""
    (bundle_dir / "cognition-a2a-tck.log").write_text(
        redact_sensitive_text(fixture_log_text),
        encoding="utf-8",
    )

    manifest: dict[str, Any] = {
        "schemaVersion": "cognition.a2a-tck-evidence.v1",
        "generatedAt": timestamp,
        "subject": "Cognition deterministic A2A adapter conformance fixture",
        "protocolVersion": "1.0",
        "cognitionVersion": cognition_version,
        "cognitionRevision": cognition_sha,
        "officialTckRepository": "https://github.com/a2aproject/a2a-tck",
        "officialTckRevision": tck_sha,
        "suite": suite,
        "transportSelection": "Agent Card supportedInterfaces",
        "command": command,
        "workflowUrl": workflow_url,
        "tckExitCode": tck_exit_code,
        "gatePolicy": "Applicable MUST requirements block release",
        "gatePassed": gate_passed,
        "mustFailures": must_failures,
        "requirementCounts": counts,
    }
    (bundle_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (bundle_dir / "COGNITION-EXPLANATION.md").write_text(
        render_explanation(
            compatibility=compatibility,
            manifest=manifest,
            requirements=requirements,
        ),
        encoding="utf-8",
    )

    archive = output_dir / f"{asset_stem}.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(bundle_dir.iterdir()):
            bundle.write(path, arcname=path.name)
    checksum = output_dir / f"{archive.name}.sha256"
    checksum.write_text(
        f"{_sha256(archive)}  {archive.name}\n",
        encoding="utf-8",
    )
    return archive, checksum, gate_passed


def redact_sensitive_text(value: str) -> str:
    """Remove bearer credentials and JWT-shaped values from captured logs."""
    redacted = _AUTHORIZATION_PATTERN.sub(r"\1<redacted>", value)
    return _JWT_PATTERN.sub("<redacted-jwt>", redacted)


def render_explanation(
    *,
    compatibility: dict[str, Any],
    manifest: dict[str, Any],
    requirements: dict[str, dict[str, Any]],
) -> str:
    """Render Cognition's stable interpretation of one official TCK run."""
    summary = compatibility.get("summary", {})
    summary = summary if isinstance(summary, dict) else {}
    counts = manifest["requirementCounts"]
    lines = [
        "# Cognition A2A v1 TCK Explanation",
        "",
        "## What this validates",
        "",
        "This report validates Cognition's A2A v1 adapter using Cognition's "
        "deterministic, test-only TCK fixture. It does not evaluate Stock Guru "
        "or any other production LLM agent, gateway, or OAuth deployment.",
        "",
        "The official A2A TCK source was used unchanged at revision "
        f"`{manifest['officialTckRevision']}`. The fixture implements the TCK's "
        "documented message-ID scenario contract while exercising Cognition's "
        "real Agent Card, JSON-RPC, persistence, task lifecycle, artifact, and "
        "streaming paths.",
        "",
        "Cognition's optional production SendMessage idempotency extension is "
        "disabled only in this fixture because the TCK reuses message IDs across "
        "independent scenarios. Production idempotency remains enabled and is "
        "covered by Cognition's own tests.",
        "",
        "## Run identity",
        "",
        f"- Cognition version: `{manifest['cognitionVersion']}`",
        f"- Cognition revision: `{manifest['cognitionRevision']}`",
        f"- A2A protocol version: `{manifest['protocolVersion']}`",
        f"- TCK revision: `{manifest['officialTckRevision']}`",
        f"- Suite: `{manifest['suite']}`",
        f"- Workflow: {manifest['workflowUrl'] or 'local/manual run'}",
        "",
        "## Compatibility result",
        "",
        f"- Overall compatibility: `{summary.get('overall_compatibility', 'unknown')}`",
        f"- MUST compatibility: `{summary.get('must_compatibility', 'unknown')}`",
        f"- SHOULD compatibility: `{summary.get('should_compatibility', 'unknown')}`",
        f"- MAY compatibility: `{summary.get('may_compatibility', 'unknown')}`",
        "",
        "| Level | PASS | FAIL | SKIPPED | NOT TESTED |",
        "|---|---:|---:|---:|---:|",
    ]
    for level in ("MUST", "SHOULD", "MAY"):
        level_counts = counts.get(level, {})
        lines.append(
            f"| {level} | {level_counts.get('PASS', 0)} | "
            f"{level_counts.get('FAIL', 0)} | {level_counts.get('SKIPPED', 0)} | "
            f"{level_counts.get('NOT TESTED', 0)} |"
        )

    failed = [
        (requirement_id, result)
        for requirement_id, result in sorted(requirements.items())
        if result.get("status") == "FAIL"
    ]
    lines.extend(["", "## Non-passing requirements", ""])
    if not failed:
        lines.append("No requirements were reported as failed.")
    else:
        for requirement_id, result in failed:
            errors = result.get("errors", [])
            explanation = "; ".join(str(error) for error in errors) or "No error detail reported"
            lines.append(
                f"- `{requirement_id}` ({result.get('level', 'UNKNOWN')}): {explanation}"
            )

    conclusion = (
        "PASS — all applicable MUST requirements passed and the TCK process completed."
        if manifest["gatePassed"]
        else "FAIL — the release-blocking A2A conformance gate did not pass."
    )
    lines.extend(
        [
            "",
            "Skipped or not-tested requirements reflect transports or optional "
            "capabilities not declared by the fixture and are not treated as failures.",
            "",
            "## Cognition conclusion",
            "",
            conclusion,
            "",
            "The release owner must review this explanation and record reviewer, "
            "date, and workflow URL in the release PR before tagging.",
            "",
        ]
    )
    return "\n".join(lines)


def _requirements(compatibility: dict[str, Any]) -> dict[str, dict[str, Any]]:
    value = compatibility.get("per_requirement")
    if not isinstance(value, dict):
        raise ValueError("A2A TCK compatibility.json is missing per_requirement")
    return {
        str(requirement_id): result
        for requirement_id, result in value.items()
        if isinstance(result, dict)
    }


def _requirement_counts(
    requirements: dict[str, dict[str, Any]],
) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = {}
    for result in requirements.values():
        level = str(result.get("level", "UNKNOWN"))
        status = str(result.get("status", "UNKNOWN"))
        counts.setdefault(level, Counter())[status] += 1
    return {
        level: dict(sorted(level_counts.items()))
        for level, level_counts in sorted(counts.items())
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--fixture-log", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cognition-version", required=True)
    parser.add_argument("--cognition-sha", required=True)
    parser.add_argument("--tck-sha", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--suite", choices=("must", "full"), required=True)
    parser.add_argument("--workflow-url", default="")
    parser.add_argument("--tck-exit-code", type=int, required=True)
    return parser.parse_args()


def main() -> int:
    """Build evidence and return the release-gate status."""
    args = parse_args()
    try:
        archive, checksum, gate_passed = build_evidence(
            reports_dir=args.reports_dir,
            fixture_log=args.fixture_log,
            output_dir=args.output_dir,
            cognition_version=args.cognition_version,
            cognition_sha=args.cognition_sha,
            tck_sha=args.tck_sha,
            command=args.command,
            suite=args.suite,
            workflow_url=args.workflow_url,
            tck_exit_code=args.tck_exit_code,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(archive)
    print(checksum)
    return 0 if gate_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
