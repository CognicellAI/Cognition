# Cognition Quick Start (5 Minutes)

Get Cognition running in under 5 minutes.

## 1. Install Dependencies (1 minute)

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install Cognition
cd cognition
uv pip install -e ".[all]"

# Build Docker image
make build-agent-image
```

## 2. Configure LLM (1 minute)

```bash
cp .env.example .env
```

Choose ONE of these options:

**Option A: OpenAI (Easiest)**
```bash
# Edit .env
echo "OPENAI_API_KEY=sk-your-key-here" >> .env
```

**Option B: Local Ollama (Free)**
```bash
# First install Ollama: https://ollama.ai
# Then: ollama run llama2
# Then edit .env:
echo "LLM_PROVIDER=openai_compatible" >> .env
echo "OPENAI_API_BASE=http://localhost:11434/v1" >> .env
echo "DEFAULT_MODEL=llama2" >> .env
```

**Option C: Anthropic**
```bash
echo "LLM_PROVIDER=anthropic" >> .env
echo "ANTHROPIC_API_KEY=sk-ant-your-key-here" >> .env
```

## 3. Start Server (1 minute)

**Terminal 1:**
```bash
make dev-server
```

Wait for:
```
INFO:     Application startup complete
```

## 4. Start Client (1 minute)

**Terminal 2:**
```bash
make dev-client
```

You'll see the Cognition TUI interface.

## 5. Try It Out (1 minute)

In the TUI, type:

**Create a persistent project (recommended):**
```
> /create my-first-project
```

**Or create an ephemeral session:**
```
> /create
```

Then:
```
> Find all Python files in the workspace
```

---

## That's It! 🎉

You now have Cognition running. Try these commands:

```
# Create a persistent project
> /create my-flask-project

# Clone a repository
> /clone https://github.com/pallets/flask.git

# Work with the agent
> Show me all the Flask route definitions
> Search for TODO comments
> Create a summary of the project structure

# Disconnect (your work is saved!)
# Later, resume with:
> /resume my-flask-project-xxxxxxxx
```

### Listing Resumable Sessions

**Via API:**
```bash
# See all projects you can reconnect to
curl http://localhost:8000/api/sessions/resumable

# Or filter projects by status
curl "http://localhost:8000/api/projects?status=resumable"
```

**In TUI:**
```
# List your recent projects
> /list

# Resume a specific project
> /resume my-flask-project-a7b3c2d1
```

**💡 Pro Tip:** Use projects (`/create <name>`) instead of ephemeral sessions (`/create`) to preserve your work across disconnects!

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Docker daemon is not running" | Start Docker Desktop or run `sudo systemctl start docker` |
| "Connection refused" | Make sure Terminal 1 has `make dev-server` running |
| "No module named..." | Run `uv pip install -e ".[all]"` again |
| "opencode-agent:py not found" | Run `make build-agent-image` |

---

## Next: Full User Guide

See `USER_GUIDE.md` for detailed configuration, examples, and troubleshooting.
