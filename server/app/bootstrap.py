"""Config bootstrap from config.yaml and workspace sources.

Seeds ``ProviderConfig`` entries into the ConfigStore on startup from the
``llm:`` section of ``.cognition/config.yaml``. Uses ``seed_if_absent``
semantics: YAML provides defaults, API rows always win.

Architecture: Layer 1 (Foundation) — startup-only, runs once during
the ``main.py`` lifespan before the server begins accepting requests.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any

import structlog
from langchain_core.tools import BaseTool

from server.app.storage.config_models import SkillDefinition, ToolRegistration

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Static mapping: provider type → default api_key_env
# ---------------------------------------------------------------------------

_PROVIDER_TYPE_TO_DEFAULT_API_KEY_ENV: dict[str, str | None] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai_compatible": "COGNITION_OPENAI_COMPATIBLE_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
    "google_vertexai": None,  # uses ADC, no key
    "bedrock": None,  # uses IAM, no key
    "mock": None,
}


def _infer_api_key_env(provider_type: str) -> str | None:
    """Return the conventional api_key_env for a provider type."""
    return _PROVIDER_TYPE_TO_DEFAULT_API_KEY_ENV.get(provider_type)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def seed_providers_from_config(
    config: dict[str, Any],
    config_store: Any,
) -> bool:
    """Seed a ProviderConfig from the ``llm:`` section of config.yaml.

    Reads ``config["llm"]`` and constructs a ``ProviderConfig`` entry
    with ``id="default"``, ``scope={}``, ``source="file"``.  Uses
    ``seed_if_absent`` so an existing API-written provider with the
    same ID is never overwritten.

    Args:
        config: The merged YAML config dict from ``load_config()``.
        config_store: Unified config persistence interface.

    Returns:
        True if a provider was seeded, False if skipped (already exists,
        missing ``llm:`` section, or missing required fields).
    """
    llm = config.get("llm")
    if not isinstance(llm, dict):
        logger.debug("No llm section in config.yaml — skipping provider bootstrap")
        return False

    provider_type = llm.get("provider")
    model = llm.get("model")

    if not provider_type or not model:
        logger.debug(
            "llm section missing provider or model — skipping provider bootstrap",
            provider=provider_type,
            model=model,
        )
        return False

    # Skip the mock provider — it's test-only
    if provider_type == "mock":
        logger.debug("llm.provider is 'mock' — skipping provider bootstrap")
        return False

    # Build the definition dict for seed_if_absent
    api_key_env = llm.get("api_key_env") or _infer_api_key_env(provider_type)
    base_url = llm.get("base_url")
    region = llm.get("region")
    role_arn = llm.get("role_arn")

    definition: dict[str, Any] = {
        "id": "default",
        "provider": provider_type,
        "model": model,
        "display_name": f"Default ({provider_type})",
        "enabled": True,
        "priority": 0,
        "max_retries": 2,
        "scope": {},
        "source": "file",
    }
    if api_key_env:
        definition["api_key_env"] = api_key_env
    if base_url:
        definition["base_url"] = base_url
    if region:
        definition["region"] = region
    if role_arn:
        definition["role_arn"] = role_arn

    try:
        inserted = await config_store.seed_if_absent(
            entity_type="provider",
            name="default",
            scope={},
            definition=definition,
            source="file",
        )

        if inserted:
            logger.info(
                "Provider seeded from config.yaml",
                provider=provider_type,
                model=model,
                base_url=base_url,
                api_key_env=api_key_env,
            )
        else:
            logger.debug(
                "Provider 'default' already exists — config.yaml bootstrap skipped",
                provider=provider_type,
                model=model,
            )
        return bool(inserted)

    except Exception as exc:
        logger.warning(
            "Failed to seed provider from config.yaml",
            error=str(exc),
            provider=provider_type,
            model=model,
        )
        return False


def _resolve_source_dir(source: str, workspace_root: Path) -> Path:
    path = Path(source)
    if path.is_absolute():
        return path
    return (workspace_root / path).resolve()


def _load_tool_file(tool_file: Path) -> list[BaseTool]:
    module_name = f"_cognition_seed_tool_{tool_file.stem}"
    file_spec = importlib.util.spec_from_file_location(module_name, str(tool_file))
    if file_spec is None or file_spec.loader is None:
        return []

    module = importlib.util.module_from_spec(file_spec)
    if module_name in sys.modules:
        del sys.modules[module_name]
    sys.modules[module_name] = module
    file_spec.loader.exec_module(module)

    resolved_tools: list[BaseTool] = []
    for _, obj in inspect.getmembers(module):
        if isinstance(obj, BaseTool):
            resolved_tools.append(obj)
    return resolved_tools


async def seed_skills_from_sources(
    config: dict[str, Any],
    config_store: Any,
    workspace_root: Path,
) -> int:
    """Seed file-managed skills from configured skill source directories."""
    from server.app.config_loader import get_skill_sources

    inserted = 0
    for source in get_skill_sources(config):
        source_dir = _resolve_source_dir(source, workspace_root)
        if not source_dir.exists() or not source_dir.is_dir():
            logger.warning("Skill source directory missing", source=source, resolved=str(source_dir))
            continue

        for skill_dir in sorted(path for path in source_dir.iterdir() if path.is_dir()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists() or not skill_md.is_file():
                continue

            content = skill_md.read_text(encoding="utf-8")
            name = skill_dir.name
            description: str | None = None
            try:
                import yaml

                if content.startswith("---\n"):
                    _, frontmatter, _ = content.split("---", 2)
                    metadata = yaml.safe_load(frontmatter)
                    if isinstance(metadata, dict):
                        name = metadata.get("name") or name
                        description = metadata.get("description")
            except Exception:
                logger.warning("Failed to parse skill frontmatter", path=str(skill_md), exc_info=True)

            definition = SkillDefinition(
                name=name,
                path=str(skill_md.relative_to(workspace_root)) if skill_md.is_relative_to(workspace_root) else str(skill_md),
                enabled=True,
                description=description,
                content=content,
                scope={},
                source="file",
            )

            existing = await config_store.get_skill(name, scope={})
            if existing and existing.source == "api":
                logger.warning("Skipping file skill seed because API-managed skill exists", name=name)
                continue

            await config_store.upsert_skill(definition)
            inserted += 1

    return inserted


async def seed_tools_from_sources(
    config: dict[str, Any],
    config_store: Any,
    workspace_root: Path,
) -> int:
    """Seed file-managed tools from configured tool source directories."""
    from server.app.config_loader import get_tool_sources

    inserted = 0
    for source in get_tool_sources(config):
        source_dir = _resolve_source_dir(source, workspace_root)
        if not source_dir.exists() or not source_dir.is_dir():
            logger.warning("Tool source directory missing", source=source, resolved=str(source_dir))
            continue

        for tool_file in sorted(path for path in source_dir.iterdir() if path.is_file() and path.suffix == ".py"):
            try:
                discovered_tools = _load_tool_file(tool_file)
            except Exception:
                logger.warning("Failed to load tool file during seeding", path=str(tool_file), exc_info=True)
                continue

            for tool in discovered_tools:
                name = tool.name
                definition = ToolRegistration(
                    name=name,
                    path=str(tool_file.relative_to(workspace_root)) if tool_file.is_relative_to(workspace_root) else str(tool_file),
                    code=None,
                    enabled=True,
                    description=getattr(tool, "description", None),
                    interrupt_on=False,
                    scope={},
                    source="file",
                )

                existing = await config_store.get_tool(name, scope={})
                if existing and existing.source == "api":
                    logger.warning("Skipping file tool seed because API-managed tool exists", name=name)
                    continue

                await config_store.upsert_tool(definition)
                inserted += 1

    return inserted
