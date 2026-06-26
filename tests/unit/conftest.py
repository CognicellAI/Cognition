"""Pytest configuration for unit tests."""

from __future__ import annotations

import importlib.util
import sys
import types
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def stub_optional_langchain_aws(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide a patchable langchain_aws module when the Bedrock extra is absent."""
    if importlib.util.find_spec("langchain_aws") is not None:
        return

    module = types.ModuleType("langchain_aws")
    module.ChatBedrock = MagicMock(name="ChatBedrock")
    monkeypatch.setitem(sys.modules, "langchain_aws", module)
