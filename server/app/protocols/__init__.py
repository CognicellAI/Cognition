"""A2A (Agent-to-Agent) protocol adapter for Cognition.

Exposes Cognition as an A2A Server so agents built on any A2A-compatible
framework can discover and invoke Cognition agents. Uses the official
a2a-sdk for protocol mechanics; Cognition owns the executor bridge,
scope enforcement, and event translation.
"""
