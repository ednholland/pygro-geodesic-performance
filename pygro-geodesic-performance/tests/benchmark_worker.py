from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from pathlib import Path
from statistics import median
from time import process_time_ns as trusted_clock_ns

import numpy as np
import sympy as sp


def _timing(function, *, calls: int, repeats: int = 7) -> float:
    for _ in range(min(calls, 300)):
        function()
    samples = []
    for _ in range(repeats):
        start = trusted_clock_ns()
        for _ in range(calls):
            function()
        samples.append((trusted_clock_ns() - start) / calls)
    return float(median(samples))


def _schwarzschild(M: float):
    from pygro import Metric

    return Metric(
        "Schwarzschild runtime metric",
        ["t", "r", "theta", "phi"],
        line_element=(
            "-(1-2*M/r)*dt**2"
            "+1/(1-2*M/r)*dr**2"
            "+r**2*(dtheta**2+sin(theta)**2*dphi**2)"
        ),
        M=M,
    )


def _kerr(M: float, a: float):
    from pygro import Metric

    sigma = "(r**2+a**2*cos(theta)**2)"
    delta = "(r**2-2*M*r+a**2)"
    line_element = (
        f"-(1-2*M*r/{sigma})*dt**2"
        f"-4*M*a*r*sin(theta)**2/{sigma}*dt*dphi"
        f"+{sigma}/{delta}*dr**2"
        f"+{sigma}*dtheta**2"
        f"+(r**2+a**2+2*M*a**2*r*sin(theta)**2/{sigma})"
        "*sin(theta)**2*dphi**2"
    )
    # Kerr's velocity-normalization helper expressions are unrelated to the
    # RHS hot path and exceptionally expensive for SymPy to factor. Replace
    # only those eight solve calls while still constructing the metric tensor,
    # inverse, Christoffels, and equations of motion from the held-out line
    # element. The shipped Kerr-BL.metric file is intentionally not used.
    original_solve = sp.solve
    sp.solve = lambda *args, **kwargs: [sp.Integer(0)]
    try:
        return Metric(
            "Kerr runtime metric",
            ["t", "r", "theta", "phi"],
            line_element=line_element,
            M=M,
            a=a,
        )
    finally:
        sp.solve = original_solve


def _functional_metric():
    from pygro import Metric

    def A(t, r, theta, phi):
        return 1.0 - 2.0 / r

    def dAdr(t, r, theta, phi):
        return 2.0 / r**2

    return Metric(
        "Python-function runtime metric",
        ["t", "r", "theta", "phi"],
        line_element=(
            "-A(r)*dt**2+dr**2/A(r)"
            "+r**2*(dtheta**2+sin(theta)**2*dphi**2)"
        ),
        A=A,
        dAdr=dAdr,
    )


def _metric_result(metric, state, *, changed_parameters, rhs_calls, step_calls):
    from pygro import GeodesicEngine
    from pygro.integrators import DormandPrince45, RungeKuttaFehlberg45

    engine = GeodesicEngine(metric, backend="autowrap", integrator="dp45")
    states = []
    for index in range(5):
        point = state.copy()
        point[1] += 0.07 * index
        point[2] += 0.003 * index
        point[5] -= 0.0002 * index
        states.append(engine.motion_eq(0.013 * index, point).tolist())

    before_change = engine.motion_eq(0.0, state).tolist()
    metric.set_constant(**changed_parameters)
    after_change = engine.motion_eq(0.0, state).tolist()
    for key, value in changed_parameters.items():
        metric.set_constant(**{key: value / 1.071})

    dp45 = DormandPrince45(
        engine.motion_eq,
        lambda *values: True,
        accuracy_goal=10,
        precision_goal=10,
    )
    rkf45 = RungeKuttaFehlberg45(
        engine.motion_eq,
        lambda *values: True,
        accuracy_goal=10,
        precision_goal=10,
    )
    dp45_step = lambda: dp45.next_step(0.0, state, 0.001, 1.0)
    rkf45_step = lambda: rkf45.next_step(0.0, state, 0.001, 1.0)
    return (
        {
            "samples": states,
            "before_change": before_change,
            "after_change": after_change,
        },
        {
            "rhs_ns": _timing(lambda: engine.motion_eq(0.0, state), calls=rhs_calls),
            "dp45_step_ns": _timing(dp45_step, calls=step_calls),
            "rkf45_step_ns": _timing(rkf45_step, calls=step_calls),
        },
        engine,
    )


def _integrator_results(engine, state):
    from pygro.integrators import get_integrator

    results = {}
    for name in ("rkf45", "dp45", "rkf78", "dp853"):
        integrator = get_integrator(
            name,
            engine.motion_eq,
            stopping_criterion=lambda *values: True,
            accuracy_goal=10,
            precision_goal=10,
        )
        next_x, next_y, next_h, stages = integrator.next_step(
            0.0, state.copy(), 0.003, 2.0
        )
        tau, values, status, dense = integrator.integrate(
            0.0, 2.0, state.copy(), 0.003
        )
        sample_tau = np.linspace(0.0, 2.0, 13)
        results[name] = {
            "next_x": next_x,
            "next_y": next_y.tolist(),
            "next_h": next_h,
            "stages": np.stack(stages).astype(float, copy=False).tolist(),
            "status": status,
            "dense": dense(sample_tau).tolist(),
            "last": values[-1].tolist(),
            "tau_last": tau[-1],
        }
    return results


def _control_flow_results():
    from pygro.integrators import DormandPrince45, RungeKuttaFehlberg45

    def oscillator(t, y):
        return np.array(
            [y[1], -0.2 * y[0], y[3], -0.3 * y[2], y[5], -0.4 * y[4], y[7], -0.5 * y[6]]
        )

    initial = np.array([1.0, -0.2, 0.5, 0.1, -0.7, 0.05, 0.3, -0.1])
    always = lambda *values: True
    forward = DormandPrince45(oscillator, always, accuracy_goal=10, precision_goal=10)
    tau_f, state_f, status_f, dense_f = forward.integrate(0.0, 1.7, initial.copy(), 0.01)
    backward = DormandPrince45(oscillator, always, accuracy_goal=10, precision_goal=10)
    tau_b, state_b, _, _ = backward.integrate(0.0, -1.3, initial.copy(), -0.01)

    class StopAtThreshold:
        exit = "threshold"

        def __call__(self, *values):
            return values[0] > 0.78

    stopped = DormandPrince45(
        oscillator,
        StopAtThreshold(),
        accuracy_goal=10,
        precision_goal=10,
    )
    _, state_s, status_s, _ = stopped.integrate(0.0, 10.0, initial.copy(), 0.01)
    return {
        "forward_status": status_f,
        "forward_tau": [tau_f[-1]],
        "forward_state": dense_f(np.linspace(0.0, 1.7, 15)).tolist(),
        "backward_tau": [tau_b[-1]],
        "backward_state": state_b[-1].tolist(),
        "stopped_status": status_s,
        "stopped_state": state_s[-1].tolist(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed & 0xFFFFFFFF)
    sys.path.insert(0, str(args.repo.resolve()))
    logging.disable(logging.CRITICAL)

    M = random.uniform(0.91, 1.09)
    spin = random.uniform(0.38, 0.72)
    radius = random.uniform(8.4, 11.6)
    theta = random.uniform(1.05, 1.38)
    state = np.array([0.17, radius, theta, 0.23, 1.18, 0.006, -0.002, 0.041])

    schwarzschild, schwarz_perf, schwarz_engine = _metric_result(
        _schwarzschild(M),
        state,
        changed_parameters={"M": M * 1.071},
        rhs_calls=12_000,
        step_calls=3_000,
    )
    kerr, kerr_perf, _ = _metric_result(
        _kerr(M, spin),
        state,
        changed_parameters={"M": M * 1.071, "a": spin * 1.071},
        rhs_calls=8_000,
        step_calls=2_000,
    )

    from pygro import GeodesicEngine
    from pygro.integrators import DormandPrince45, RungeKuttaFehlberg45

    functional_engine = GeodesicEngine(_functional_metric(), backend="autowrap")
    functional = {
        "rhs": functional_engine.motion_eq(0.0, state).tolist(),
        "wrapper_is_lambdify": [float(functional_engine._wrapper == "lambdify")],
    }

    def synthetic_rhs(t, y):
        return np.array(
            [y[4], y[5], y[6], y[7], -0.01 * y[0], -0.02 * y[1], -0.03 * y[2], -0.04 * y[3]]
        )

    synthetic_dp45 = DormandPrince45(synthetic_rhs, lambda *values: True)
    synthetic_rkf45 = RungeKuttaFehlberg45(synthetic_rhs, lambda *values: True)
    synthetic_dp45_step = lambda: synthetic_dp45.next_step(0.0, state, 0.001, 1.0)
    synthetic_rkf45_step = lambda: synthetic_rkf45.next_step(0.0, state, 0.001, 1.0)

    output = {
        "correctness": {
            "schwarzschild": schwarzschild,
            "kerr": kerr,
            "functional": functional,
            "integrators": _integrator_results(schwarz_engine, state),
            "control_flow": _control_flow_results(),
        },
        "performance": {
            "schwarzschild": schwarz_perf,
            "kerr": kerr_perf,
            "synthetic": {
                "dp45_step_ns": _timing(synthetic_dp45_step, calls=5_000),
                "rkf45_step_ns": _timing(synthetic_rkf45_step, calls=5_000),
            },
        },
    }
    args.output.write_text(json.dumps(output), encoding="utf-8")


if __name__ == "__main__":
    main()
