from __future__ import annotations

from deepagents.backends.composite import CompositeBackend

from server.app.agent.sandbox_backend import CognitionLocalSandboxBackend


class StubSkillBackend:
    def ls(self, path: str):
        return [{"path": "/demo/", "is_dir": True, "size": 0, "modified_at": ""}]

    def ls_info(self, path: str):
        return [{"path": "/demo/", "is_dir": True, "size": 0, "modified_at": ""}]

    def glob_info(self, pattern: str, path: str = "/"):
        return []

    def read(self, file_path: str, offset: int = 0, limit: int = 2000):
        return "stub"

    def grep_raw(self, pattern: str, path: str | None = None, glob: str | None = None):
        return []


def test_composite_backend_routes_only_skill_paths(tmp_path):
    repo_root = tmp_path / "workspace"
    repo_root.mkdir()
    (repo_root / "Cognition-Gateway").mkdir()
    (repo_root / "Cognition-Gateway" / "README.md").write_text("hello", encoding="utf-8")

    sandbox = CognitionLocalSandboxBackend(root_dir=repo_root)
    backend = CompositeBackend(default=sandbox, routes={"/skills/api/": StubSkillBackend()})

    repo_entries = backend.ls_info(str(repo_root / "Cognition-Gateway"))
    assert any(
        entry["path"] == str(repo_root / "Cognition-Gateway" / "README.md")
        for entry in repo_entries
    )

    skill_entries = backend.ls_info("/skills/api/")
    assert skill_entries == [
        {"path": "/skills/api/demo/", "is_dir": True, "size": 0, "modified_at": ""}
    ]
