# Deployment Guide

Complete guide for deploying Cognition in production environments.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [Production Deployment](#production-deployment)
5. [Docker Deployment](#docker-deployment)
6. [Monitoring & Observability](#monitoring--observability)
7. [Backup & Recovery](#backup--recovery)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### System Requirements

**Minimum:**
- CPU: 2 cores
- RAM: 4GB
- Disk: 20GB
- OS: Linux (Ubuntu 20.04+ recommended), macOS, or Windows with WSL2

**Recommended:**
- CPU: 4+ cores
- RAM: 8GB+
- Disk: 50GB+ SSD
- Network: Stable internet connection for LLM APIs

### Software Requirements

- **Docker** 20.10+ with Docker Compose
- **Python** 3.11+
- **Git**
- **Nginx** (for reverse proxy, optional but recommended)

### LLM Provider Setup

Choose one provider:

#### Option 1: OpenAI (Recommended for production)
```bash
# Get API key from https://platform.openai.com/api-keys
export OPENAI_API_KEY=sk-your-production-key
```

#### Option 2: AWS Bedrock (Enterprise)
```bash
# Configure AWS credentials
aws configure
# Or use IAM role if running on EC2/ECS
```

#### Option 3: Self-hosted (Privacy-focused)
```bash
# Set up vLLM or Ollama on separate GPU instance
# Requires GPU with 16GB+ VRAM for good performance
```

---

## Installation

### Method 1: Direct Installation

```bash
# Clone repository
git clone https://github.com/yourusername/cognition.git
cd cognition

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv pip install -e ".[all]"

# Build Docker image
make build-agent-image

# Verify installation
uv run pytest tests/ -q
```

### Method 2: Docker Compose (Recommended)

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  cognition:
    build:
      context: .
      dockerfile: docker/Dockerfile.server
    ports:
      - "8000:8000"
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - HOST=0.0.0.0
      - PORT=8000
      - WORKSPACE_ROOT=/app/workspaces
      - MAX_SESSIONS=100
      - MAX_PROJECTS=500
      - PROJECT_CLEANUP_ENABLED=true
      - PROJECT_CLEANUP_AFTER_DAYS=30
      - MEMORY_SNAPSHOT_ENABLED=true
      - AGENT_BACKEND_ROUTES={"/workspace/":{"type":"filesystem"},"/memories/hot/":{"type":"store"},"/memories/persistent/":{"type":"filesystem"},"/tmp/":{"type":"state"}}
    volumes:
      - ./workspaces:/app/workspaces
      - /var/run/docker.sock:/var/run/docker.sock
    restart: unless-stopped
    networks:
      - cognition-network

  # Optional: Nginx reverse proxy
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./ssl:/etc/nginx/ssl:ro
    depends_on:
      - cognition
    restart: unless-stopped
    networks:
      - cognition-network

networks:
  cognition-network:
    driver: bridge
```

Deploy:

```bash
# Create .env file
cat > .env << EOF
OPENAI_API_KEY=sk-your-production-key
HOST=0.0.0.0
PORT=8000
EOF

# Start services
docker-compose up -d

# Verify
curl http://localhost:8000/health
```

---

## Configuration

### Environment Variables

Create `.env` file:

```bash
# Server Configuration
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=info
DEBUG=false

# LLM Configuration
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-your-production-key
DEFAULT_MODEL=gpt-4-turbo-preview

# Workspace & Projects
WORKSPACE_ROOT=/app/workspaces
MAX_SESSIONS=100
MAX_PROJECTS=500

# Auto-Cleanup
PROJECT_CLEANUP_ENABLED=true
PROJECT_CLEANUP_AFTER_DAYS=30
PROJECT_CLEANUP_WARNING_DAYS=3
PROJECT_CLEANUP_CHECK_INTERVAL=86400

# Memory Persistence
MEMORY_SNAPSHOT_ENABLED=true
MEMORY_SNAPSHOT_INTERVAL=300

# Container Lifecycle
CONTAINER_STOP_ON_DISCONNECT=true
CONTAINER_RECREATE_ON_RECONNECT=true
CONTAINER_TIMEOUT=300
CONTAINER_MEMORY_LIMIT=2g
CONTAINER_CPU_LIMIT=1.0

# Backend Routes (Hybrid Memory Strategy)
AGENT_BACKEND_ROUTES='{
  "/workspace/": {"type": "filesystem"},
  "/memories/hot/": {"type": "store"},
  "/memories/persistent/": {"type": "filesystem"},
  "/tmp/": {"type": "state"}
}'

# Observability (Optional)
OTEL_EXPORTER_OTLP_ENDPOINT=https://otel-collector.example.com:4317
OTEL_SERVICE_NAME=cognition-production
```

### Security Configuration

#### 1. Firewall Rules

```bash
# Allow only necessary ports
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp   # SSH
sudo ufw allow 80/tcp   # HTTP
sudo ufw allow 443/tcp  # HTTPS
sudo ufw enable
```

#### 2. SSL/TLS with Let's Encrypt

```bash
# Install certbot
sudo apt-get install certbot python3-certbot-nginx

# Obtain certificate
sudo certbot --nginx -d cognition.yourdomain.com

# Auto-renewal
sudo certbot renew --dry-run
```

#### 3. Nginx Configuration

Create `nginx.conf`:

```nginx
events {
    worker_connections 1024;
}

http {
    map $http_upgrade $connection_upgrade {
        default upgrade;
        '' close;
    }

    upstream cognition {
        server cognition:8000;
    }

    server {
        listen 80;
        server_name cognition.yourdomain.com;
        return 301 https://$server_name$request_uri;
    }

    server {
        listen 443 ssl http2;
        server_name cognition.yourdomain.com;

        ssl_certificate /etc/nginx/ssl/fullchain.pem;
        ssl_certificate_key /etc/nginx/ssl/privkey.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers HIGH:!aNULL:!MD5;

        # WebSocket support
        location /ws {
            proxy_pass http://cognition/ws;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $connection_upgrade;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_read_timeout 86400;
        }

        # REST API
        location /api {
            proxy_pass http://cognition/api;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # Health check
        location /health {
            proxy_pass http://cognition/health;
            access_log off;
        }
    }
}
```

---

## Production Deployment

### 1. Pre-deployment Checklist

- [ ] All tests passing (`uv run pytest`)
- [ ] Docker image built (`make build-agent-image`)
- [ ] Environment variables configured
- [ ] SSL certificates installed
- [ ] Firewall configured
- [ ] Monitoring setup
- [ ] Backup strategy in place

### 2. Deployment Steps

```bash
# 1. Pull latest code
git pull origin main

# 2. Run tests
uv run pytest tests/ -q

# 3. Build fresh image
make build-agent-image

# 4. Deploy
docker-compose up -d

# 5. Verify health
curl https://cognition.yourdomain.com/health

# 6. Check logs
docker-compose logs -f cognition
```

### 3. Zero-Downtime Deployment

```bash
# Using blue-green deployment
# 1. Start new container on different port
docker-compose -f docker-compose.yml -f docker-compose.new.yml up -d cognition-new

# 2. Verify new container is healthy
sleep 10
curl http://localhost:8001/health

# 3. Switch Nginx to new container
# Update nginx.conf upstream to point to new port
sudo nginx -s reload

# 4. Stop old container
docker-compose stop cognition

# 5. Rename new to old
docker-compose rename cognition-new cognition
```

---

## Docker Deployment

### Building Production Image

```dockerfile
# docker/Dockerfile.server
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

# Copy project files
COPY pyproject.toml .
COPY server/ server/
COPY client/ client/

# Install dependencies
RUN uv pip install -e ".[all]"

# Create workspace directory
RUN mkdir -p /app/workspaces

# Expose port
EXPOSE 8000

# Run server
CMD ["uv", "run", "uvicorn", "server.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build and push:

```bash
# Build
docker build -t your-registry/cognition:latest -f docker/Dockerfile.server .

# Push
docker push your-registry/cognition:latest

# Deploy on production server
docker pull your-registry/cognition:latest
docker-compose up -d
```

---

## Monitoring & Observability

### Health Checks

```bash
# Basic health check
curl https://cognition.yourdomain.com/health

# Expected response:
{
  "status": "healthy",
  "version": "0.1.0",
  "sessions_active": 5,
  "llm": {
    "configured": true,
    "provider": "openai"
  }
}
```

### Logging

Configure structured logging:

```bash
# View logs
docker-compose logs -f cognition

# Or with journald
sudo journalctl -u cognition -f
```

### Metrics (Optional)

Prometheus metrics endpoint (add to your app):

```python
from prometheus_client import Counter, Histogram

# Define metrics
sessions_created = Counter('cognition_sessions_created_total', 'Total sessions created')
request_duration = Histogram('cognition_request_duration_seconds', 'Request duration')
```

### Alerting Rules

Example Prometheus alerts:

```yaml
groups:
  - name: cognition
    rules:
      - alert: CognitionDown
        expr: up{job="cognition"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Cognition server is down"

      - alert: HighMemoryUsage
        expr: container_memory_usage_bytes{name="cognition"} > 6e+09
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Cognition memory usage is high"

      - alert: ManyActiveSessions
        expr: cognition_sessions_active > 80
        for: 10m
        labels:
          severity: info
        annotations:
          summary: "High number of active sessions"
```

---

## Backup & Recovery

### What to Backup

1. **Workspaces** (`./workspaces/` directory)
2. **Project metadata** (`.project_metadata.json` files)
3. **Environment configuration** (`.env` file)

### Automated Backup Script

Create `backup.sh`:

```bash
#!/bin/bash

BACKUP_DIR="/backup/cognition"
DATE=$(date +%Y%m%d_%H%M%S)

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Backup workspaces
tar -czf "$BACKUP_DIR/workspaces_$DATE.tar.gz" ./workspaces/

# Backup environment
cp .env "$BACKUP_DIR/env_$DATE"

# Keep only last 7 backups
ls -t "$BACKUP_DIR"/*.tar.gz | tail -n +8 | xargs -r rm
ls -t "$BACKUP_DIR"/env_* | tail -n +8 | xargs -r rm

echo "Backup completed: $DATE"
```

Schedule with cron:

```bash
# Daily backup at 2 AM
0 2 * * * /path/to/cognition/backup.sh >> /var/log/cognition-backup.log 2>&1
```

### Recovery Procedure

```bash
# 1. Stop services
docker-compose down

# 2. Restore workspaces
tar -xzf /backup/cognition/workspaces_20240210_020000.tar.gz

# 3. Restore environment
cp /backup/cognition/env_20240210_020000 .env

# 4. Restart services
docker-compose up -d

# 5. Verify
curl https://cognition.yourdomain.com/health
```

---

## Troubleshooting

### Server Won't Start

**Check logs:**
```bash
docker-compose logs cognition | tail -50
```

**Common issues:**
- Missing environment variables
- Docker socket not accessible
- Port already in use

### LLM API Errors

**Check LLM configuration:**
```bash
# Test OpenAI API
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"

# Test AWS credentials
aws sts get-caller-identity
```

### Container Execution Fails

**Verify Docker:**
```bash
docker ps
docker images | grep opencode-agent

# Test container creation manually
docker run --rm opencode-agent:py echo "test"
```

### High Memory Usage

**Monitor:**
```bash
# Check container stats
docker stats cognition --no-stream

# Check workspace size
du -sh workspaces/

# Clean up old projects
curl -X DELETE https://cognition.yourdomain.com/api/projects/old-project-id
```

### Slow Performance

**Check:**
1. LLM provider latency (switch to faster provider)
2. Container resource limits (increase CPU/memory)
3. Network connectivity
4. Disk I/O (use SSD)

**Tune settings:**
```bash
# Increase container resources
CONTAINER_MEMORY_LIMIT=4g
CONTAINER_CPU_LIMIT=2.0
CONTAINER_TIMEOUT=600

# Faster LLM
DEFAULT_MODEL=gpt-3.5-turbo  # Instead of gpt-4
```

### WebSocket Connection Issues

**Test WebSocket:**
```bash
# Test with wscat
npm install -g wscat
wscat -c ws://localhost:8000/ws

# Check Nginx config
sudo nginx -t
sudo systemctl reload nginx
```

---

## Scaling

### Horizontal Scaling

For high load, deploy multiple instances:

```yaml
# docker-compose.scale.yml
version: '3.8'

services:
  cognition-1:
    extends:
      file: docker-compose.yml
      service: cognition
    ports:
      - "8001:8000"

  cognition-2:
    extends:
      file: docker-compose.yml
      service: cognition
    ports:
      - "8002:8000"

  nginx:
    extends:
      file: docker-compose.yml
      service: nginx
    depends_on:
      - cognition-1
      - cognition-2
```

Update Nginx for load balancing:

```nginx
upstream cognition {
    least_conn;
    server cognition-1:8000;
    server cognition-2:8000;
}
```

---

## Security Best Practices

1. **Use strong API keys** - Rotate regularly
2. **Enable SSL/TLS** - Always use HTTPS in production
3. **Restrict network access** - Firewall rules
4. **Regular updates** - Keep dependencies updated
5. **Monitor logs** - Watch for suspicious activity
6. **Backup regularly** - Automated daily backups
7. **Use secrets management** - Don't commit secrets to git

---

## Support

For deployment issues:
- Check logs: `docker-compose logs -f`
- Test health: `curl /health`
- Review documentation
- Check GitHub issues

---

**You're now ready to deploy Cognition in production! 🚀**
