.PHONY: help install build-agent-image test lint format typecheck clean docker-up docker-down docker-logs

help:
	@echo "Available targets:"
	@echo "  install         - Install dependencies with uv"
	@echo "  build-agent-image - Build the Docker agent image"
	@echo "  test            - Run all tests"
	@echo "  test-integration - Run integration tests"
	@echo "  lint            - Run ruff linter"
	@echo "  format          - Format code with ruff"
	@echo "  typecheck       - Run mypy type checker"
	@echo "  clean           - Clean build artifacts"
	@echo "  dev-server      - Run development server"
	@echo "  dev-client      - Run development client"
	@echo "  docker-up       - Start Docker Compose (Cognition + Observability)"
	@echo "  docker-down     - Stop Docker Compose"
	@echo "  docker-logs     - View Docker Compose logs"

install:
	uv pip install -e ".[all]"

build-agent-image:
	docker build -t opencode-agent:py -f docker/Dockerfile.agent .

test:
	uv run pytest -q

test-integration:
	uv run pytest -m integration -v

test-e2e:
	uv run pytest -m e2e -v

lint:
	uv run ruff check server/ client/

format:
	uv run ruff format server/ client/
	uv run ruff check --fix server/ client/

typecheck:
	uv run mypy server/ client/ --strict

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	rm -rf build/ dist/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

dev-server:
	cd server && uv run uvicorn app.main:app --reload --port 8000

dev-client:
	cd client && uv run python -m tui.app

pre-commit:
	uv run pre-commit run --all-files

# Docker Compose targets for observability stack
docker-up:
	docker-compose up -d
	@echo "Services starting up..."
	@echo "Cognition API: http://localhost:8000"
	@echo "Grafana: http://localhost:3000 (admin/admin)"
	@echo "Jaeger UI: http://localhost:16686"
	@echo "Prometheus: http://localhost:9091"

docker-down:
	docker-compose down

docker-logs:
	docker-compose logs -f

docker-build:
	docker-compose build --no-cache
