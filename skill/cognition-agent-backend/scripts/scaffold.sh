#!/bin/bash

# Scaffold a new Cognition project
# Usage: ./scaffold.sh [project_name]

set -e

PROJECT_NAME=${1:-"my-cognition-agent"}
TARGET_DIR="$PWD/$PROJECT_NAME"

echo "🚀 Scaffolding Cognition project in $TARGET_DIR..."

if [ -d "$TARGET_DIR" ]; then
    echo "❌ Directory $TARGET_DIR already exists."
    exit 1
fi

mkdir -p "$TARGET_DIR/.cognition/skills"
mkdir -p "$TARGET_DIR/app/tools"
mkdir -p "$TARGET_DIR/tests"

cd "$TARGET_DIR"

# 1. Create .env
cat > .env <<EOF
# Cognition Environment Configuration
COGNITION_LLM_PROVIDER=openai
COGNITION_LLM_MODEL=gpt-4o
OPENAI_API_KEY=sk-your-key-here

# Persistence (SQLite for dev)
COGNITION_PERSISTENCE_BACKEND=sqlite
COGNITION_PERSISTENCE_URI=.cognition/state.db

# Execution (Local for dev)
COGNITION_SANDBOX_BACKEND=local
EOF

# 2. Create .cognition/config.yaml
cat > .cognition/config.yaml <<EOF
server:
  host: 0.0.0.0
  port: 8000
  log_level: info

agent:
  memory:
    - "AGENTS.md"
  skills:
    - ".cognition/skills/"
  interrupt_on:
    execute: true
EOF

# 3. Create AGENTS.md (if not exists)
if [ -f "AGENTS.md" ]; then
    echo "ℹ️  AGENTS.md already exists. Skipping to preserve your guidelines."
else
    cat > AGENTS.md <<EOF
# Project Guidelines

## Code Style
- Use Python 3.11+ type hinting
- Follow PEP 8
- Write docstrings for all public functions

## Testing
- Run 'pytest' before committing
- Mock external API calls
EOF
fi

# 4. Create example tool
cat > app/tools/example.py <<EOF
def hello_world(name: str) -> str:
    """A simple example tool."""
    return f"Hello, {name}!"
EOF

# 5. Create agent definition
cat > .cognition/agent.yaml <<EOF
name: $PROJECT_NAME
system_prompt: |
  You are a helpful coding assistant for the $PROJECT_NAME project.
tools:
  - "app.tools.example.hello_world"
EOF

# 6. Create README
cat > README.md <<EOF
# $PROJECT_NAME

Powered by Cognition AI Agent Backend.

## Setup

1. Install dependencies:
   \`pip install cognition-agent\`

2. Configure environment:
   Edit \`.env\` with your API keys.

3. Run server:
   \`cognition serve\`

## Development

- **Tools**: Add new tools in \`app/tools/\`
- **Skills**: Add skills in \`.cognition/skills/\`
- **Rules**: Update \`AGENTS.md\`
EOF

echo "✅ Project scaffolded successfully!"
echo "👉 Next steps:"
echo "   cd $PROJECT_NAME"
echo "   pip install cognition-agent"
echo "   cognition serve"
