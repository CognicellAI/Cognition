#!/usr/bin/env python3
"""Performance benchmark script for Cognition.

Measures key performance metrics:
- Session creation time
- Container startup time
- File I/O operations
- Memory snapshot performance
- API response times

Run with: uv run python scripts/benchmark_performance.py
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class BenchmarkResult:
    """Result of a benchmark test."""

    name: str
    duration_ms: float
    success: bool
    details: dict[str, Any] = field(default_factory=dict)


class PerformanceBenchmark:
    """Run performance benchmarks."""

    def __init__(self, api_url: str = "http://localhost:8000") -> None:
        self.api_url = api_url
        self.results: list[BenchmarkResult] = []

    async def benchmark_project_creation(self) -> BenchmarkResult:
        """Benchmark project creation time."""
        import aiohttp

        start = time.perf_counter()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/api/projects",
                    json={"user_prefix": "benchmark-test", "network_mode": "OFF"},
                ) as resp:
                    data = await resp.json()
                    elapsed = (time.perf_counter() - start) * 1000

                    return BenchmarkResult(
                        name="Project Creation",
                        duration_ms=elapsed,
                        success=resp.status == 200,
                        details={"project_id": data.get("project_id")},
                    )
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            return BenchmarkResult(
                name="Project Creation",
                duration_ms=elapsed,
                success=False,
                details={"error": str(e)},
            )

    async def benchmark_session_creation(self) -> BenchmarkResult:
        """Benchmark session creation time."""
        import aiohttp

        # First create a project
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.api_url}/api/projects",
                json={"user_prefix": "session-benchmark", "network_mode": "OFF"},
            ) as resp:
                data = await resp.json()
                project_id = data["project_id"]

        start = time.perf_counter()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/api/projects/{project_id}/sessions",
                    json={"network_mode": "OFF"},
                ) as resp:
                    data = await resp.json()
                    elapsed = (time.perf_counter() - start) * 1000

                    return BenchmarkResult(
                        name="Session Creation",
                        duration_ms=elapsed,
                        success=resp.status == 200,
                        details={
                            "project_id": project_id,
                            "session_id": data.get("session_id"),
                        },
                    )
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            return BenchmarkResult(
                name="Session Creation",
                duration_ms=elapsed,
                success=False,
                details={"error": str(e)},
            )

    async def benchmark_api_listing(self) -> BenchmarkResult:
        """Benchmark API listing performance."""
        import aiohttp

        start = time.perf_counter()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.api_url}/api/projects") as resp:
                    data = await resp.json()
                    elapsed = (time.perf_counter() - start) * 1000

                    return BenchmarkResult(
                        name="API List Projects",
                        duration_ms=elapsed,
                        success=resp.status == 200,
                        details={"count": len(data.get("projects", []))},
                    )
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            return BenchmarkResult(
                name="API List Projects",
                duration_ms=elapsed,
                success=False,
                details={"error": str(e)},
            )

    def benchmark_file_io(self) -> BenchmarkResult:
        """Benchmark file I/O performance."""
        import tempfile

        start = time.perf_counter()

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                test_file = Path(tmpdir) / "test.txt"
                content = "x" * 1000  # 1KB

                # Write
                write_start = time.perf_counter()
                for _ in range(100):
                    test_file.write_text(content)
                write_time = (time.perf_counter() - write_start) * 1000

                # Read
                read_start = time.perf_counter()
                for _ in range(100):
                    _ = test_file.read_text()
                read_time = (time.perf_counter() - read_start) * 1000

                elapsed = (time.perf_counter() - start) * 1000

                return BenchmarkResult(
                    name="File I/O (100x 1KB)",
                    duration_ms=elapsed,
                    success=True,
                    details={
                        "write_time_ms": write_time,
                        "read_time_ms": read_time,
                        "write_per_op_ms": write_time / 100,
                        "read_per_op_ms": read_time / 100,
                    },
                )
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            return BenchmarkResult(
                name="File I/O",
                duration_ms=elapsed,
                success=False,
                details={"error": str(e)},
            )

    async def run_all(self) -> None:
        """Run all benchmarks."""
        print("🚀 Starting Cognition Performance Benchmarks\n")

        # Check server health
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.api_url}/health") as resp:
                    if resp.status != 200:
                        print("❌ Server not healthy. Start it with: make dev-server")
                        return
                    print("✅ Server is healthy\n")
        except Exception:
            print("❌ Server not available at localhost:8000")
            print("   Start it with: make dev-server")
            return

        # Run benchmarks
        print("Running benchmarks...\n")

        self.results.append(await self.benchmark_project_creation())
        self.results.append(await self.benchmark_session_creation())
        self.results.append(await self.benchmark_api_listing())
        self.results.append(self.benchmark_file_io())

        # Print results
        self._print_results()

    def _print_results(self) -> None:
        """Print benchmark results."""
        print("=" * 70)
        print("BENCHMARK RESULTS")
        print("=" * 70)
        print()

        # Group by category
        api_tests = [r for r in self.results if "API" in r.name or "Creation" in r.name]
        io_tests = [r for r in self.results if "I/O" in r.name]

        if api_tests:
            print("API Performance:")
            print("-" * 70)
            for result in api_tests:
                status = "✅" if result.success else "❌"
                print(f"{status} {result.name:.<50} {result.duration_ms:>8.2f} ms")
                if result.details:
                    for key, value in result.details.items():
                        if key != "error":
                            print(f"   {key}: {value}")
            print()

        if io_tests:
            print("File I/O Performance:")
            print("-" * 70)
            for result in io_tests:
                status = "✅" if result.success else "❌"
                print(f"{status} {result.name:.<50} {result.duration_ms:>8.2f} ms")
                if result.details:
                    for key, value in result.details.items():
                        if key != "error":
                            print(f"   {key}: {value:.3f} ms")
            print()

        # Summary
        print("=" * 70)
        print("SUMMARY")
        print("=" * 70)
        total_tests = len(self.results)
        passed_tests = sum(1 for r in self.results if r.success)
        avg_time = sum(r.duration_ms for r in self.results) / total_tests

        print(f"Total Tests: {total_tests}")
        print(f"Passed: {passed_tests}")
        print(f"Failed: {total_tests - passed_tests}")
        print(f"Average Time: {avg_time:.2f} ms")
        print()

        # Performance grades
        print("Performance Grades:")
        for result in self.results:
            if not result.success:
                grade = "F (Failed)"
            elif result.duration_ms < 100:
                grade = "A+ (Excellent)"
            elif result.duration_ms < 500:
                grade = "A (Good)"
            elif result.duration_ms < 1000:
                grade = "B (Acceptable)"
            elif result.duration_ms < 5000:
                grade = "C (Slow)"
            else:
                grade = "D (Very Slow)"

            print(f"  {result.name:.<50} {grade}")

        print()
        print("=" * 70)


async def main() -> None:
    """Main entry point."""
    benchmark = PerformanceBenchmark()
    await benchmark.run_all()


if __name__ == "__main__":
    asyncio.run(main())
