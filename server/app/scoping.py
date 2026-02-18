"""Session scoping utilities.

Provides generic session scoping via configurable headers.
Sessions can be scoped by any number of dimensions (user, project, team, etc.).
"""

from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException, status
from server.app.settings import Settings


class SessionScope:
    """Represents the scope for a session.
    
    Scopes are key-value pairs extracted from request headers.
    Multiple scope dimensions can be active simultaneously.
    """
    
    def __init__(self, scopes: dict[str, str]):
        """Initialize scope with key-value pairs.
        
        Args:
            scopes: Dictionary of scope keys to values.
        """
        self._scopes = scopes
    
    def get(self, key: str) -> Optional[str]:
        """Get a scope value by key."""
        return self._scopes.get(key)
    
    def get_all(self) -> dict[str, str]:
        """Get all scope key-value pairs."""
        return self._scopes.copy()
    
    def matches(self, other_scopes: dict[str, str]) -> bool:
        """Check if this scope matches another scope dictionary.
        
        Returns True if all keys in this scope match the other scope.
        """
        for key, value in self._scopes.items():
            if other_scopes.get(key) != value:
                return False
        return True
    
    def is_empty(self) -> bool:
        """Check if scope is empty."""
        return len(self._scopes) == 0
    
    def __repr__(self) -> str:
        return f"SessionScope({self._scopes})"


def extract_scope_from_headers(
    settings: Settings,
    **header_values: Optional[str]
) -> SessionScope:
    """Extract scope from request headers.
    
    Args:
        settings: Application settings with scope_keys configuration.
        **header_values: Header values keyed by scope key name.
        
    Returns:
        SessionScope with extracted values.
    """
    scopes = {}
    for key in settings.scope_keys:
        value = header_values.get(key)
        if value:
            scopes[key] = value
    return SessionScope(scopes)


def create_scope_dependency(settings: Settings):
    """Create a FastAPI dependency for extracting session scope.
    
    Returns a dependency function that:
    1. Extracts scope values from headers based on configured scope_keys
    2. Enforces fail-closed behavior when scoping is enabled
    
    Args:
        settings: Application settings.
        
    Returns:
        Dependency function for FastAPI.
    """
    # Build the list of header parameters dynamically
    header_params = {}
    for key in settings.scope_keys:
        header_name = f"x-cognition-scope-{key.replace('_', '-')}"
        header_params[key] = Header(None, alias=header_name)
    
    async def scope_dependency(**headers) -> SessionScope:
        """Extract scope from headers with fail-closed validation."""
        scope = extract_scope_from_headers(settings, **headers)
        
        # Fail-closed: if scoping is enabled, require all scope keys
        if settings.scoping_enabled:
            missing_keys = [
                key for key in settings.scope_keys
                if not scope.get(key)
            ]
            if missing_keys:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Missing required scope headers: {missing_keys}. "
                           f"Expected headers: {[f'X-Cognition-Scope-{k.replace(\"_\", \"-\").title()}' for k in missing_keys]}"
                )
        
        return scope
    
    # Set the signature to match the dynamic headers
    import inspect
    params = [
        inspect.Parameter(
            name=key,
            kind=inspect.Parameter.KEYWORD_ONLY,
            default=Header(None, alias=f"x-cognition-scope-{key.replace('_', '-')}")
        )
        for key in settings.scope_keys
    ]
    scope_dependency.__signature__ = inspect.Signature(params)  # type: ignore
    
    return scope_dependency
