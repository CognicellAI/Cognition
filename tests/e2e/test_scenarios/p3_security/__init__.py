"""P3-SEC Business Scenarios: Security Hardening.

As a security engineer deploying Cognition in production,
I want multiple layers of security controls
so that malicious code cannot compromise the system.

Business Value:
- AST scanning prevents dangerous imports in custom tools
- Path confinement prevents directory traversal attacks
- Tool namespace allowlist prevents unauthorized tool loading
- .cognition/ directory protection prevents self-modification attacks
- CORS tightening prevents cross-site request forgery

P3-SEC Items Covered:
- P3-SEC-1: AST Import Scanning
- P3-SEC-2: Protect .cognition/ from Agent Writes
- P3-SEC-3: Path Confinement (str.startswith → Path.is_relative_to)
- P3-SEC-4: Tool Module Allowlist
- P3-SEC-5: CORS Default Tightening
"""
