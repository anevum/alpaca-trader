from __future__ import annotations

import argparse

from app.research_agent.multiplicity import simulate_multiplicity_benchmark


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run MATH-001-C multiplicity benchmark."
    )
    parser.add_argument("--replicates", type=int, default=400)
    parser.add_argument("--hypotheses", type=int, default=40)
    parser.add_argument("--nonnull", type=int, default=5)
    parser.add_argument("--effect", type=float, default=2.5)
    parser.add_argument("--rho", type=float, default=0.65)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--e-theta", type=float, default=1.0)
    parser.add_argument("--async-lag", type=int, default=3)
    parser.add_argument("--seed", type=int, default=1002)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = simulate_multiplicity_benchmark(
        replicates=args.replicates,
        hypothesis_count=args.hypotheses,
        nonnull_count=args.nonnull,
        effect=args.effect,
        rho=args.rho,
        alpha=args.alpha,
        e_theta=args.e_theta,
        async_lag=args.async_lag,
        seed=args.seed,
    )
    print(result.to_json())


if __name__ == "__main__":
    main()
