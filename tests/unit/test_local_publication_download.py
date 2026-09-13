"""Bounded local-development publication reads preserve path and memory limits."""
import os

import pytest

from server.app.agent.sandbox_backend import CognitionLocalSandboxBackend


def test_regular_binary_and_limit(tmp_path):
    backend = CognitionLocalSandboxBackend(tmp_path)
    target = tmp_path / "file.bin"
    target.write_bytes(b"\x00\xffabc")
    assert backend.download_file_bounded(str(target), 5) == b"\x00\xffabc"
    with pytest.raises(ValueError, match="byte limit"):
        backend.download_file_bounded(str(target), 4)
    target.write_bytes(b"")
    assert backend.download_file_bounded(str(target), 0) == b""


@pytest.mark.parametrize("kind", ["leaf", "directory", "fifo", "outside", "traversal"])
def test_rejects_unsafe_sources(tmp_path, kind):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"private")
    backend = CognitionLocalSandboxBackend(root)
    target = root / "output"
    if kind == "leaf":
        target.symlink_to(outside)
    elif kind == "directory":
        target.symlink_to(tmp_path, target_is_directory=True)
        target = target / "outside"
    elif kind == "fifo":
        os.mkfifo(target)
    elif kind == "outside":
        target = outside
    else:
        target = root / ".." / "outside"
    with pytest.raises((ValueError, OSError)):
        backend.download_file_bounded(str(target), 100)


def test_read_limit_remains_enforced_after_size_check(tmp_path, monkeypatch):
    import stat
    from types import SimpleNamespace

    backend = CognitionLocalSandboxBackend(tmp_path)
    target = tmp_path / "growing"
    target.write_bytes(b"too large")
    with monkeypatch.context() as patch:
        patch.setattr(os, "fstat", lambda _: SimpleNamespace(st_mode=stat.S_IFREG, st_size=0))
        with pytest.raises(ValueError, match="byte limit"):
            backend.download_file_bounded(str(target), 3)
