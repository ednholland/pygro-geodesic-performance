from __future__ import annotations

import json
import math
import os
import secrets
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest


BASELINE_REPO = Path("/opt/pygro-baseline")
CANDIDATE_REPO = Path("/app")
WORKER = Path("/tests/benchmark_worker.py")


def _run_worker(repo: Path, output: Path, seed: int) -> dict:
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = str(seed)
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(WORKER),
            "--repo",
            str(repo),
            "--output",
            str(output),
            "--seed",
            str(seed),
        ],
        cwd="/tmp",
        env=env,
        text=True,
        capture_output=True,
        timeout=360,
    )
    if completed.returncode != 0:
        pytest.fail(
            f"benchmark worker failed for {repo} (exit {completed.returncode})\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return json.loads(output.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def run_results(tmp_path_factory):
    seed = secrets.randbelow(1_000_000_000) + 1
    workdir = tmp_path_factory.mktemp("pygro-comparison")
    baseline = _run_worker(BASELINE_REPO, workdir / "baseline.json", seed)
    candidate = _run_worker(CANDIDATE_REPO, workdir / "candidate.json", seed)
    return baseline, candidate


def _assert_close(actual, expected, *, rtol=2e-10, atol=2e-12):
    np.testing.assert_allclose(
        np.asarray(actual, dtype=float),
        np.asarray(expected, dtype=float),
        rtol=rtol,
        atol=atol,
    )


def test_runtime_defined_metric_rhs_and_parameter_updates(run_results):
    baseline, candidate = run_results
    for metric_name in ("schwarzschild", "kerr", "functional"):
        expected = baseline["correctness"][metric_name]
        actual = candidate["correctness"][metric_name]
        assert actual.keys() == expected.keys()
        for key in expected:
            _assert_close(actual[key], expected[key])


def test_adaptive_integrators_preserve_steps_and_dense_output(run_results):
    baseline, candidate = run_results
    expected = baseline["correctness"]["integrators"]
    actual = candidate["correctness"]["integrators"]
    assert actual.keys() == expected.keys()
    for name in expected:
        assert actual[name]["status"] == expected[name]["status"] == "done"
        _assert_close(actual[name]["next_x"], expected[name]["next_x"])
        _assert_close(actual[name]["next_y"], expected[name]["next_y"])
        _assert_close(actual[name]["next_h"], expected[name]["next_h"], rtol=2e-9)
        _assert_close(actual[name]["stages"], expected[name]["stages"], rtol=2e-10)
        _assert_close(
            actual[name]["dense"],
            expected[name]["dense"],
            rtol=2e-6,
            atol=5e-8,
        )


def test_backward_integration_and_stopping_semantics(run_results):
    baseline, candidate = run_results
    expected = baseline["correctness"]["control_flow"]
    actual = candidate["correctness"]["control_flow"]
    assert actual["forward_status"] == expected["forward_status"]
    assert actual["stopped_status"] == expected["stopped_status"] == "threshold"
    _assert_close(actual["forward_tau"], expected["forward_tau"], rtol=2e-9)
    _assert_close(actual["forward_state"], expected["forward_state"], rtol=2e-7, atol=2e-9)
    _assert_close(actual["backward_tau"], expected["backward_tau"], rtol=2e-9)
    _assert_close(actual["backward_state"], expected["backward_state"], rtol=2e-7, atol=2e-9)
    _assert_close(actual["stopped_state"], expected["stopped_state"], rtol=5e-5, atol=5e-7)


def _speedup(baseline: dict, candidate: dict, metric: str, measurement: str) -> float:
    return baseline["performance"][metric][measurement] / candidate["performance"][metric][measurement]


def test_rhs_evaluation_is_substantially_faster(run_results):
    baseline, candidate = run_results
    speedups = [
        _speedup(baseline, candidate, metric, "rhs_ns")
        for metric in ("schwarzschild", "kerr")
    ]
    assert min(speedups) >= 2.20, f"per-metric RHS speedups were {speedups}"
    geometric_mean = math.prod(speedups) ** (1.0 / len(speedups))
    assert geometric_mean >= 3.20, f"RHS geometric-mean speedup was {geometric_mean:.3f}x"


def test_runge_kutta_driver_is_substantially_faster(run_results):
    baseline, candidate = run_results
    speedups = [
        baseline["performance"]["synthetic"][measurement]
        / candidate["performance"]["synthetic"][measurement]
        for measurement in ("dp45_step_ns", "rkf45_step_ns")
    ]
    assert min(speedups) >= 1.60, f"adaptive-driver speedups were only {speedups}"
    geometric_mean = math.prod(speedups) ** (1.0 / len(speedups))
    assert geometric_mean >= 1.80, f"driver geometric-mean speedup was {geometric_mean:.3f}x"


def test_end_to_end_geodesic_step_is_substantially_faster(run_results):
    baseline, candidate = run_results
    speedups = [
        _speedup(baseline, candidate, metric, measurement)
        for metric in ("schwarzschild", "kerr")
        for measurement in ("dp45_step_ns", "rkf45_step_ns")
    ]
    assert min(speedups) >= 2.35, f"integration-step speedups were {speedups}"
    geometric_mean = math.prod(speedups) ** (1.0 / len(speedups))
    assert geometric_mean >= 3.00, f"step geometric-mean speedup was {geometric_mean:.3f}x"
