from __future__ import annotations

import argparse

from app.research_agent.dependence import simulate_dependence_benchmark


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run MATH-001-D dependence benchmark."
    )
    parser.add_argument("--replicates", type=int, default=500)
    parser.add_argument("--candidates", type=int, default=20)
    parser.add_argument("--observations", type=int, default=120)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--common-factor-weight", type=float, default=0.8)
    parser.add_argument("--ar-phi", type=float, default=0.75)
    parser.add_argument("--seed", type=int, default=1003)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = simulate_dependence_benchmark(
        replicates=args.replicates,
        candidates_per_replicate=args.candidates,
        observations_per_candidate=args.observations,
        alpha=args.alpha,
        common_factor_weight=args.common_factor_weight,
        ar_phi=args.ar_phi,
        seed=args.seed,
    )
    print(result.to_json())


if __name__ == "__main__":
    main()
