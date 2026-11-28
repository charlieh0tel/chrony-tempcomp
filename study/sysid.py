"""System identification of how the clock's frequency follows its
temperature: output-error models, y = c + B(q)/F(q) [u (+ a u^2)],
fitted robustly, their orders and dead time chosen by blocked
cross-validation, against the static fit tempcomp's own line is.

Usage: python3 sysid.py HOST.npz...
"""

import sys

import numpy as np
from common import (
    HUBER,
    MAD_SIGMA,
    STEP,
    cross_validate,
    design,
    fit_predictor,
    load,
)
from scipy.optimize import least_squares
from scipy.signal import lfilter, lfilter_zi

# Orders of B and F, and dead times in samples, tried.
B_ORDERS = (1, 2)
F_ORDERS = (1, 2)
DELAYS = (0, 1, 3, 6, 12)
# Minutes after a step of the temperature its response is reported at.
REPORT_AT = (5, 30, 120)
# Samples of a step response taken as its end.
SETTLED = 2000
# A model's shape: B's order, F's, the dead time in samples, quadratic.
Model = tuple[int, int, int, bool]


def simulate(
    p: np.ndarray, u: np.ndarray, nb: int, nf: int, delay: int, quadratic: bool
) -> np.ndarray:
    """The model's frequency from the temperature alone; p is c, B's
    coefficients, F's, then the quadratic's, started settled."""
    b, f = p[1 : 1 + nb], p[1 + nb : 1 + nb + nf]
    x = u + (p[-1] * u * u if quadratic else 0)
    if delay:
        x = np.concatenate([np.full(delay, x[0]), x[:-delay]])
    den = np.concatenate([[1], f])
    simulated: np.ndarray = p[0] + lfilter(b, den, x, zi=lfilter_zi(b, den) * x[0])[0]
    return simulated


def fit(
    u: np.ndarray,
    y: np.ndarray,
    train: np.ndarray,
    nb: int,
    nf: int,
    delay: int,
    quadratic: bool,
) -> tuple[np.ndarray, bool]:
    slope, intercept = np.polyfit(u[train], y[train], 1)
    p0 = np.concatenate(
        [
            [intercept],
            [slope * 0.5 / nb] * nb,
            [-0.5] + [0] * (nf - 1),
            [0] if quadratic else [],
        ]
    )

    def residuals(p: np.ndarray) -> np.ndarray:
        residual: np.ndarray = (y - simulate(p, u, nb, nf, delay, quadratic))[train]
        return residual

    r = residuals(p0)
    scale = MAD_SIGMA * np.median(np.abs(r - np.median(r))) or 1e-9
    p: np.ndarray = least_squares(
        residuals, p0, loss="huber", f_scale=HUBER * scale, max_nfev=400
    ).x
    stable = np.all(
        np.abs(np.roots(np.concatenate([[1], p[1 + nb : 1 + nb + nf]]))) < 0.999
    )
    return p, bool(stable)


def step_response(
    p: np.ndarray, nb: int, nf: int, delay: int
) -> tuple[float, list[float]]:
    """The static gain, ppm/°C, and the share of a step reached at each
    of REPORT_AT."""
    b, f = p[1 : 1 + nb], p[1 + nb : 1 + nb + nf]
    step = lfilter(
        b, np.concatenate([[1], f]), np.concatenate([np.zeros(delay), np.ones(SETTLED)])
    )
    return float(b.sum() / (1 + f.sum())), [
        float(step[(m * 60) // STEP] / step[-1]) for m in REPORT_AT
    ]


def main() -> None:
    for path in sys.argv[1:]:
        t, u, y, good = load(path)
        print(f"=== {path}: {(t[-1] - t[0]) / 86400:.1f} days")
        for quadratic in (False, True):
            name = "quadratic" if quadratic else "linear"
            x = design(u, quadratic)
            static = cross_validate(fit_predictor(x, y), good, y)
            best: tuple[float, np.ndarray, int, int, int] | None = None
            for nb in B_ORDERS:
                for nf in F_ORDERS:
                    for delay in DELAYS:
                        model: Model = (nb, nf, delay, quadratic)

                        def predict(
                            train: np.ndarray,
                            u: np.ndarray = u,
                            y: np.ndarray = y,
                            model: Model = model,
                        ) -> np.ndarray:
                            return simulate(fit(u, y, train, *model)[0], u, *model)

                        score = cross_validate(predict, good, y)
                        p, stable = fit(u, y, good, *model)
                        if stable and (best is None or score < best[0]):
                            best = (score, p, nb, nf, delay)
            if best is None:
                raise ValueError(f"{path}: no stable {name} model")
            score, p, nb, nf, delay = best
            gain, shares = step_response(p, nb, nf, delay)
            print(
                f" {name:9} static held-out RMSE {static:.4f} ppm; best model nb {nb} nf {nf} "
                f"dead time {delay * STEP // 60} min: {score:.4f}, gain {gain:+.4f} ppm/°C, step reached at "
                + ", ".join(f"{m} min {s:.2f}" for m, s in zip(REPORT_AT, shares))
            )


if __name__ == "__main__":
    main()
