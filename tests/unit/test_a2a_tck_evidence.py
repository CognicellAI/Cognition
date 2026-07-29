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
    must_requirement: str = "CORE-SEND-001",
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
            must_requirement: {
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
    must_requirement: str = "CORE-SEND-001",
    must_status: str = "PASS",
    should_status: str = "PASS",
    tck_exit_code: int = 0,
) -> tuple[Path, Path, bool]:
    reports = _write_reports(
        tmp_path,
        must_requirement=must_requirement,
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
    archive, checksum, conformance_passed = _build(tmp_path)

    assert conformance_passed is True
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
    assert manifest["conformancePassed"] is True
    assert "Stock Guru was not identified as the cause" in explanation
    assert "PASS — all applicable MUST requirements passed" in explanation
    assert "secret-token" not in log
    assert "eyJhbGci" not in log


def test_must_or_process_failure_is_reported_as_nonconformant(tmp_path: Path) -> None:
    _archive, _checksum, must_result = _build(tmp_path / "must", must_status="FAIL")
    _archive, _checksum, process_result = _build(tmp_path / "process", tck_exit_code=1)

    assert must_result is False
    assert process_result is False


def test_known_core_send_003_tck_defect_is_explained_not_waived(tmp_path: Path) -> None:
    reports = _write_reports(
        tmp_path,
        must_requirement="CORE-SEND-003",
        must_status="FAIL",
    )
    log = tmp_path / "fixture.log"
    log.write_text("", encoding="utf-8")

    archive, _checksum, conformance_passed = build_evidence(
        reports_dir=reports,
        fixture_log=log,
        output_dir=tmp_path / "evidence",
        cognition_version="0.13.1",
        cognition_sha="cognition-sha",
        tck_sha="5996b79f9cefa6fc390980e383e358a66fb9e49e",
        command="run_tck.py --level must",
        suite="must",
        workflow_url="https://github.example/run/1",
        tck_exit_code=1,
    )

    assert conformance_passed is False
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        explanation = bundle.read("COGNITION-EXPLANATION.md").decode()
    assessment = manifest["findingAssessments"]["CORE-SEND-003"]
    assert assessment["classification"] == "upstream-tck-defect"
    assert assessment["fixability"] == (
        "not fixable in Cognition without violating A2A v1"
    )
    assert "a2aproject/a2a-tck/issues/202" in explanation


def test_unknown_failure_requires_cognition_owner_review(tmp_path: Path) -> None:
    archive, _checksum, _result = _build(tmp_path, must_status="FAIL")

    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
    assessment = manifest["findingAssessments"]["CORE-SEND-001"]
    assert assessment["classification"] == "unassessed-finding"
    assert assessment["fixability"] == "unknown; Cognition owner review required"


def test_should_failure_is_advisory_when_tck_process_succeeds(tmp_path: Path) -> None:
    archive, _checksum, conformance_passed = _build(tmp_path, should_status="FAIL")

    assert conformance_passed is True
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
