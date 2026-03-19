"""End-to-end tests for Provider Fallback functionality.

This package contains comprehensive e2e tests for the provider fallback system,
testing real Agent interactions with the docker-compose environment.

Prerequisites:
1. Docker and docker-compose must be installed
2. Run `docker-compose up -d` to start the environment
3. Set COGNITION_OPENAI_COMPATIBLE_API_KEY in .env for full provider tests

Test Coverage:
- Provider CRUD operations via API
- Priority-based fallback chains
- Scoped configurations (multi-tenancy)
- Agent interactions with fallback
- Hot-reloading of providers
- Credential resolution
- Error handling and edge cases

Test Markers:
- @pytest.mark.e2e: All e2e tests
- @pytest.mark.provider_fallback: Provider-specific tests
- @pytest.mark.scoped: Multi-tenant scope tests
- @pytest.mark.credentials: Tests requiring API credentials
"""
