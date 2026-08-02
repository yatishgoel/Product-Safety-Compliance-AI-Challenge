"""Run the corpus as a Phoenix experiment.

The corpus becomes a versioned Phoenix dataset and each run an experiment, so changing a
layer and re-running gives a side-by-side comparison with per-case diffs. That is the
recall / review-rate curve an operating point gets chosen from, produced as a by-product
rather than hand-rolled.

Prereqs:  uv sync --extra obs
          PHOENIX_COLLECTOR_ENDPOINT and PHOENIX_API_KEY set, the same pair tracing uses.
          The client talks to whatever that endpoint is, hosted or your own. `phoenix serve`
          is not available here: the obs extra installs the client, not the server.

Usage:    uv run python -m eval.run_experiment --name "baseline exact-match"
"""

from __future__ import annotations

import argparse

from eval.harness import load_cases, run_case


# Phoenix binds evaluator arguments by name and accepts a plain dict of
# score / label / explanation.
def catches_forbidden(output: dict, expected: dict) -> dict:
    """Recall on banned substances - the metric a compliance team is judged on."""
    if expected["status"] != "Rejected":
        return {"score": 1.0, "label": "n/a"}
    caught = output["status"] in ("Rejected", "Needs Review")
    return {
        "score": float(caught),
        "label": "caught" if caught else "MISSED",
        "explanation": f"verdict {output['status']}" if caught
        else "banned substance present but product was ACCEPTED",
    }


def no_false_reject(output: dict, expected: dict) -> dict:
    if expected["status"] != "Accepted":
        return {"score": 1.0, "label": "n/a"}
    ok = output["status"] != "Rejected"
    return {
        "score": float(ok),
        "label": "ok" if ok else "FALSE REJECT",
        "explanation": "; ".join(output.get("reason", [])),
    }


def cites_correctly(output: dict, expected: dict) -> dict:
    required = {c.lower() for c in (expected.get("must_cite") or [])}
    if not required:
        return {"score": 1.0, "label": "n/a"}
    missed = required - {c.lower() for c in output.get("cited", [])}
    return {
        "score": float(not missed),
        "label": "cited" if not missed else "WRONG REASON",
        "explanation": f"missed {sorted(missed)}" if missed else "all required entries named",
    }


def abstained(output: dict) -> dict:
    """Tracked as a cost, not a failure - it drives the recall / review-rate curve."""
    review = output["status"] == "Needs Review"
    return {"score": float(review), "label": "review" if review else "auto"}


EVALUATORS = [catches_forbidden, no_false_reject, cites_correctly, abstained]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="baseline exact-match")
    ap.add_argument("--dataset", default="compliance-hard-cases-v1")
    args = ap.parse_args()

    from phoenix.client import Client

    cases = load_cases()
    by_id = {c["id"]: c for c in cases}
    client = Client()

    try:
        dataset = client.datasets.get_dataset(dataset=args.dataset)
        print(f"reusing dataset {args.dataset}")
    except Exception:
        dataset = client.datasets.create_dataset(
            name=args.dataset,
            inputs=[{"case_id": c["id"], "tier": c["tier"], "title": c["title"]} for c in cases],
            outputs=[{"status": c["expect"]["status"],
                      "must_cite": c["expect"].get("must_cite") or []} for c in cases],
        )
        print(f"uploaded dataset {args.dataset} ({len(cases)} cases)")

    def task(input: dict) -> dict:
        v = run_case(by_id[input["case_id"]]).verdict
        return {
            "status": v.status.value,
            "reason": list(v.reason),
            "cited": sorted(v.entries),
            "unverified": list(v.unverified),
            "index_version": v.index_version,
        }

    client.experiments.run_experiment(
        dataset=dataset, task=task, evaluators=EVALUATORS, experiment_name=args.name
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
