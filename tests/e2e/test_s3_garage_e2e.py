"""End-to-end coverage for the S3 backend against a real Garage node."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from botocore.config import Config

from server.app.agent.s3_backend import S3CompatibleBackend

pytestmark = pytest.mark.e2e

_ACCESS_KEY = "cognition-test-access"
_SECRET_KEY = "cognition-test-secret"
_BUCKET = "cognition-test"


@pytest.fixture(scope="module")
def garage_endpoint(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Start an ephemeral single-node Garage S3 service for this test module."""
    docker = pytest.importorskip("docker")
    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:  # pragma: no cover - local environment condition
        pytest.skip(f"Docker is unavailable for Garage E2E: {type(exc).__name__}")

    config_path = Path(tmp_path_factory.mktemp("garage")) / "garage.toml"
    config_path.write_text(
        "\n".join(
            [
                'metadata_dir = "/tmp/meta"',
                'data_dir = "/tmp/data"',
                'db_engine = "sqlite"',
                "replication_factor = 1",
                'rpc_bind_addr = "[::]:3901"',
                'rpc_public_addr = "127.0.0.1:3901"',
                'rpc_secret = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"',
                "[s3_api]",
                's3_region = "garage"',
                'api_bind_addr = "[::]:3900"',
                'root_domain = ".s3.garage.localhost"',
            ]
        ),
        encoding="utf-8",
    )
    container = client.containers.run(
        "dxflrs/garage:v2.3.0",
        "/garage server --single-node --default-bucket",
        detach=True,
        remove=True,
        ports={"3900/tcp": None},
        environment={
            "GARAGE_DEFAULT_ACCESS_KEY": _ACCESS_KEY,
            "GARAGE_DEFAULT_SECRET_KEY": _SECRET_KEY,
            "GARAGE_DEFAULT_BUCKET": _BUCKET,
        },
        volumes={str(config_path): {"bind": "/etc/garage.toml", "mode": "ro"}},
    )
    try:
        container.reload()
        port = container.attrs["NetworkSettings"]["Ports"]["3900/tcp"][0]["HostPort"]
        endpoint = f"http://127.0.0.1:{port}"
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name="garage",
            aws_access_key_id=_ACCESS_KEY,
            aws_secret_access_key=_SECRET_KEY,
            config=Config(s3={"addressing_style": "path"}),
        )
        deadline = time.monotonic() + 30
        while True:
            try:
                s3.list_objects_v2(Bucket=_BUCKET)
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
        yield endpoint
    finally:
        try:
            container.stop(timeout=5)
        except Exception:
            pass


def test_s3_backend_against_garage(
    garage_endpoint: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Garage behaves as an S3-compatible durable filesystem backend."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", _ACCESS_KEY)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", _SECRET_KEY)
    backend = S3CompatibleBackend.from_boto3(
        bucket=_BUCKET,
        prefix="scope/a0b1c2",
        endpoint_url=garage_endpoint,
        region_name="garage",
        force_path_style=True,
    )

    assert backend.write("/skills/release/SKILL.md", "garage e2e").error is None
    assert backend.read("/skills/release/SKILL.md").file_data == {
        "content": "garage e2e",
        "encoding": "utf-8",
    }
    assert backend.download_files(["/skills/release/SKILL.md"])[0].content == b"garage e2e"
