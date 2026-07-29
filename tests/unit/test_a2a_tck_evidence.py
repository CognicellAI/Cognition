"""Tests for release-bound A2A TCK evidence generation."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from scripts.a2a_tck_evidence import build_evidence, redact_sensitive_text


def _write_reports(
    root: Path,
    *,
    must_status: str = "PASS",
    should_status: str = "PASS",
) -> Path:
    reports = root / "reports"
    reports.mkdir(parents=True)
    compatibility = {
        "summary": {
            "overall_compatibility": "100.0%",
            "must_compatibility": "100.0%" if must_status == "PASS" else "0.0%",
            "should_compatibility": "100.0%" if should_status == "PASS" else "0.0%",
            "may_compatibility": "100.0%",
        },
        "per_requirement": {
            "CORE-SEND-001": {
                "level": "MUST",
                "status": must_status,
                "errors": [] if must_status == "PASS" else ["wrong response"],
            },
            "CORE-HIST-001": {
                "level": "SHOULD",
                "status": should_status,
                "errors": [] if should_status == "PASS" else ["history omitted"],
            },
            "CORE-PUSH-001": {
                "level": "MAY",
                "status": "SKIPPED",
                "errors": [],
            },
        },
    }
    (reports / "compatibility.json").write_text(json.dumps(compatibility), encoding="utf-8")
    for name in ("compatibility.html", "tck_report.html", "junitreport.xml"):
        (reports / name).write_text(name, encoding="utf-8")
    return reports


def _build(
    tmp_path: Path,
    *,
    must_status: str = "PASS",
    should_status: str = "PASS",
    tck_exit_code: int = 0,
) -> tuple[Path, Path, bool]:
    reports = _write_reports(
        tmp_path,
        must_status=must_status,
        should_status=should_status,
    )
    log = tmp_path / "fixture.log"
    log.write_text(
        "Authorization: Bearer secret-token\n"
        "jwt=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature\n",
        encoding="utf-8",
    )
    return build_evidence(
        reports_dir=reports,
        fixture_log=log,
        output_dir=tmp_path / "evidence",
        cognition_version="0.13.0",
        cognition_sha="cognition-sha",
        tck_sha="tck-sha",
        command="run_tck.py --level must",
        suite="must",
        workflow_url="https://github.example/run/1",
        tck_exit_code=tck_exit_code,
        generated_at="2026-07-29T00:00:00+00:00",
    )


def test_builds_versioned_bundle_manifest_explanation_and_checksum(tmp_path: Path) -> None:
    archive, checksum, gate_passed = _build(tmp_path)

    assert gate_passed is True
    assert archive.name == "cognition-v0.13.0-a2a-v1-tck.zip"
    expected_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert checksum.read_text(encoding="utf-8") == f"{expected_digest}  {archive.name}\n"

    with zipfile.ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == {
            "COGNITION-EXPLANATION.md",
            "cognition-a2a-tck.log",
            "compatibility.html",
            "compatibility.json",
            "junitreport.xml",
            "manifest.json",
            "tck_report.html",
        }
        manifest = json.loads(bundle.read("manifest.json"))
        explanation = bundle.read("COGNITION-EXPLANATION.md").decode()
        log = bundle.read("cognition-a2a-tck.log").decode()

    assert manifest["cognitionRevision"] == "cognition-sha"
    assert manifest["officialTckRevision"] == "tck-sha"
    assert manifest["gatePassed"] is True
    assert "does not evaluate Stock Guru" in explanation
    assert "PASS — all applicable MUST requirements passed" in explanation
    assert "secret-token" not in log
    assert "eyJhbGci" not in log


def test_must_failure_or_tck_process_failure_blocks_gate(tmp_path: Path) -> None:
    _archive, _checksum, must_gate = _build(tmp_path / "must", must_status="FAIL")
    _archive, _checksum, process_gate = _build(tmp_path / "process", tck_exit_code=1)

    assert must_gate is False
    assert process_gate is False


def test_should_failure_is_advisory_when_tck_process_succeeds(tmp_path: Path) -> None:
    archive, _checksum, gate_passed = _build(tmp_path, should_status="FAIL")

    assert gate_passed is True
    with zipfile.ZipFile(archive) as bundle:
        explanation = bundle.read("COGNITION-EXPLANATION.md").decode()
    assert "`CORE-HIST-001` (SHOULD): history omitted" in explanation


def test_missing_or_malformed_compatibility_report_is_rejected(tmp_path: Path) -> None:
    reports = _write_reports(tmp_path)
    (reports / "compatibility.json").write_text("{", encoding="utf-8")

    with pytest.raises(ValueError, match="unreadable or malformed"):
        build_evidence(
            reports_dir=reports,
            fixture_log=tmp_path / "missing.log",
            output_dir=tmp_path / "evidence",
            cognition_version="0.13.0",
            cognition_sha="sha",
            tck_sha="tck",
            command="run",
            suite="full",
            workflow_url="",
            tck_exit_code=0,
        )


def test_redacts_authorization_and_jwt_values() -> None:
    value = (
        "authorization=Bearer abc123\n"
        "value eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature remains\n"
    )

    redacted = redact_sensitive_text(value)

    assert "abc123" not in redacted
    assert "eyJhbGci" not in redacted
