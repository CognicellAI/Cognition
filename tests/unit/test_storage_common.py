"""Shared storage helper behavior."""

from __future__ import annotations

from server.app.storage.common import (
    effective_scope_key,
    inherited_scope_candidates,
    inherited_scope_keys,
)


def test_inherited_scope_candidates_are_deterministic_subsets() -> None:
    candidates = inherited_scope_candidates(
        {"tenant": "acme", "project": "red", "user": "alice"}
    )

    assert candidates == [
        {},
        {"project": "red"},
        {"tenant": "acme"},
        {"user": "alice"},
        {"project": "red", "tenant": "acme"},
        {"project": "red", "user": "alice"},
        {"tenant": "acme", "user": "alice"},
        {"project": "red", "tenant": "acme", "user": "alice"},
    ]


def test_inherited_scope_keys_match_candidate_scope_hashes() -> None:
    scope = {"tenant": "acme", "project": "red"}

    assert inherited_scope_keys(scope) == [
        effective_scope_key(candidate)
        for candidate in inherited_scope_candidates(scope)
    ]

