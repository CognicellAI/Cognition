# P3-SEC Security Business Scenarios

End-to-end tests for P3-SEC (Security Hardening) features.

## Overview

These scenarios test Cognition's security features from a business value perspective, ensuring that malicious code cannot compromise the system through multiple layers of defense.

## Structure

```
tests/e2e/test_scenarios/p3_security/
├── __init__.py
├── test_ast_security_scanning.py      # P3-SEC-1: AST Import Scanning
├── test_cognition_protection.py        # P3-SEC-2 & P3-SEC-3: Path Protection
└── test_namespace_cors_security.py     # P3-SEC-4 & P3-SEC-5: Namespace & CORS
```

## P3-SEC Items Covered

### P3-SEC-1: AST Import Scanning (`test_ast_security_scanning.py`)
**Business Value:** Prevents arbitrary code execution via malicious tool files

**Scenarios:**
- Clean tools load successfully
- Security violations are logged
- Strict mode blocks dangerous imports
- Warn mode allows with logging
- Comprehensive banned modules list
- Performance: <1ms for 200-line file

**Banned Modules:**
- `os`, `subprocess`, `socket`, `ctypes`, `sys`
- `shutil`, `importlib`, `pty`, `signal`
- `multiprocessing`, `threading`, `concurrent`
- `code`, `codeop`, `builtins`

### P3-SEC-2: Protect .cognition/ from Agent Writes (`test_cognition_protection.py`)
**Business Value:** Prevents self-modification attacks and privilege escalation

**Scenarios:**
- Cannot write to `.cognition/tools/`
- Cannot write to `.cognition/agents/`
- Cannot modify `.cognition/config.yaml`
- Protected paths are configurable
- Normal workspace writes allowed

### P3-SEC-3: Path Confinement (`test_cognition_protection.py`)
**Business Value:** Prevents directory traversal attacks

**Scenarios:**
- Path `/workspace-extra` blocked when root is `/workspace`
- Absolute path traversal blocked (`/etc/passwd`)
- Relative path traversal blocked (`../../etc`)
- Symlink traversal blocked
- Null byte injection blocked

### P3-SEC-4: Tool Module Allowlist (`test_namespace_cors_security.py`)
**Business Value:** Prevents loading arbitrary Python modules

**Scenarios:**
- Trusted namespace configured by default (`server.app.tools`)
- Built-in tools load from trusted namespace
- Untrusted tool paths rejected
- Allowlist is extensible via settings

### P3-SEC-5: CORS Default Tightening (`test_namespace_cors_security.py`)
**Business Value:** Prevents CSRF attacks from malicious websites

**Scenarios:**
- CORS not wildcard by default
- Preflight requests handled correctly
- Unauthorized origins blocked
- Authorized origins allowed
- Proper CORS headers present

## Running Tests

### Against Docker Compose

```bash
# Start the environment
docker-compose up -d

# Run all P3-SEC tests
BASE_URL=http://localhost:8000 pytest tests/e2e/test_scenarios/p3_security/ -v

# Run specific test file
BASE_URL=http://localhost:8000 pytest tests/e2e/test_scenarios/p3_security/test_ast_security_scanning.py -v

# Run specific scenario
BASE_URL=http://localhost:8000 pytest tests/e2e/test_scenarios/p3_security/test_cognition_protection.py::TestCognitionDirectoryProtection -v
```

### With Coverage

```bash
# Run with coverage report
BASE_URL=http://localhost:8000 pytest tests/e2e/test_scenarios/p3_security/ -v --cov=server.app.security
```

## Security Test Categories

### Positive Tests (Allowed Operations)
- Clean tool files load successfully
- Normal workspace file writes succeed
- Trusted namespace tools load
- Authorized CORS origins work

### Negative Tests (Blocked Operations)
- Tools with banned imports blocked
- Writes to `.cognition/` blocked
- Path traversal attempts blocked
- Untrusted tool namespaces rejected
- Unauthorized CORS origins blocked

### Audit Tests (Logging & Monitoring)
- Security violations logged
- Audit trail with file paths
- Timestamps on security events
- Structured logging format

## Business Value Summary

| P3-SEC Item | Security Control | Attack Prevented | Business Impact |
|-------------|------------------|------------------|-----------------|
| P3-SEC-1 | AST Scanning | Code injection via tools | Server compromise |
| P3-SEC-2 | Path Protection | Self-modification | Tool/agent poisoning |
| P3-SEC-3 | Path Confinement | Directory traversal | File system access |
| P3-SEC-4 | Namespace Allowlist | Arbitrary module loading | Privilege escalation |
| P3-SEC-5 | CORS Tightening | CSRF attacks | Unauthorized API access |

## Related Documentation

- [AGENTS.md](../../../../AGENTS.md) - Security guidelines
- [ROADMAP.md](../../../../ROADMAP.md) - P3-SEC feature roadmap
- [config.example.yaml](../../../../config.example.yaml) - Security configuration
