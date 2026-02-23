#!/usr/bin/env python3

import importlib.util
import os
import socket
import sys


def check_python_version():
    print("Checking Python version...", end=" ")
    if sys.version_info >= (3, 11):
        print("✅ OK")
        return True
    else:
        print("❌ FAILED (Requires Python 3.11+)")
        return False


def check_dependencies():
    print("Checking core dependencies...", end=" ")
    missing = []
    for package in ["fastapi", "uvicorn", "pydantic", "langchain", "structlog"]:
        if importlib.util.find_spec(package) is None:
            missing.append(package)

    if not missing:
        print("✅ OK")
        return True
    else:
        print(f"❌ FAILED (Missing: {', '.join(missing)})")
        print("   Run: pip install cognition-agent")
        return False


def check_port(port: int = 8000):
    print(f"Checking port {port} availability...", end=" ")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("localhost", port)) == 0:
            print(f"❌ FAILED (Port {port} is in use)")
            return False
        else:
            print("✅ OK")
            return True


def check_env_vars():
    print("Checking environment configuration...", end=" ")
    provider = os.getenv("COGNITION_LLM_PROVIDER", "mock")

    if provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        print("❌ FAILED (OPENAI_API_KEY missing for 'openai' provider)")
        return False
    elif provider == "bedrock" and not (os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_PROFILE")):
        print("❌ FAILED (AWS credentials missing for 'bedrock' provider)")
        return False

    print(f"✅ OK (Provider: {provider})")
    return True


def main():
    print("🏥 Cognition Agent Doctor\n")

    checks = [check_python_version(), check_dependencies(), check_port(), check_env_vars()]

    if all(checks):
        print("\n✨ System is healthy and ready to run!")
        sys.exit(0)
    else:
        print("\n⚠️  Issues detected. Please resolve them before starting.")
        sys.exit(1)


if __name__ == "__main__":
    main()
