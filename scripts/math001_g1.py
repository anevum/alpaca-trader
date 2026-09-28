from __future__ import annotations

import argparse

from app.research_agent.math001 import simulate_null_search


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run MATH-001-G1 pure-null repeated-search simulation."
    )
    parser.add_argument("--replicates", type=int, default=500)
    parser.add_argument("--candidates", type=int, default=50)
    parser.add_argument("--observations", type=int, default=100)
    parser.add_argument("--min-observations", type=int, default=10)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=1001)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = simulate_null_search(
        replicates=args.replicates,
        candidates_per_replicate=args.candidates,
        observations_per_candidate=args.observations,
        min_observations=args.min_observations,
        alpha=args.alpha,
        seed=args.seed,
    )
    print(result.to_json())


if __name__ == "__main__":
    main()
