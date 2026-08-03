from __future__ import annotations

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import LsResult

from server.app.agent.sandbox_backend import CognitionLocalSandboxBackend


class StubSkillBackend:
    def ls(self, path: str):
        return LsResult(entries=[{"path": "/demo/", "is_dir": True, "size": 0, "modified_at": ""}])


def test_composite_backend_routes_only_skill_paths(tmp_path):
    repo_root = tmp_path / "workspace"
    repo_root.mkdir()
    (repo_root / "Cognition-Gateway").mkdir()
    (repo_root / "Cognition-Gateway" / "README.md").write_text("hello", encoding="utf-8")

    sandbox = CognitionLocalSandboxBackend(root_dir=repo_root)
    backend = CompositeBackend(default=sandbox, routes={"/skills/api/": StubSkillBackend()})

    repo_listing = backend.ls(str(repo_root / "Cognition-Gateway"))
    assert repo_listing.entries is not None
    assert any(
        entry["path"] == str(repo_root / "Cognition-Gateway" / "README.md")
        for entry in repo_listing.entries
    )

    skill_listing = backend.ls("/skills/api/")
    assert skill_listing.entries == [
        {"path": "/skills/api/demo/", "is_dir": True, "size": 0, "modified_at": ""}
    ]
