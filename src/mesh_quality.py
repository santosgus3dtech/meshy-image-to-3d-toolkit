"""Turn sanitized mesh measurements into a print-readiness report."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class MeshAssessment:
    score: int
    status: str
    checks: list[dict[str, str | bool]]
    recommendations: list[str]

    def as_dict(self) -> dict:
        return asdict(self)


def assess(metrics: dict) -> MeshAssessment:
    checks = []
    recommendations = []

    def check(name: str, passed: bool, detail: str, recommendation: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})
        if not passed:
            recommendations.append(recommendation)

    check(
        "Watertight shell",
        bool(metrics.get("watertight")),
        "Closed manifold mesh" if metrics.get("watertight") else "Open boundaries detected",
        "Run voxel repair or close boundary loops before slicing.",
    )
    components = int(metrics.get("components", 0))
    check(
        "Connected geometry",
        components == 1,
        f"{components} connected component(s)",
        "Remove floating components or join intentional parts explicitly.",
    )
    volume = float(metrics.get("volume_cm3", 0))
    check(
        "Positive volume",
        volume > 0,
        f"{volume:.2f} cm³",
        "Repair normals and confirm that the shell encloses a positive volume.",
    )
    thin_ratio = float(metrics.get("thin_wall_ratio", 1))
    check(
        "Wall screening",
        thin_ratio <= 0.02,
        f"{thin_ratio * 100:.1f}% of sampled surface below threshold",
        "Thicken fragile regions or choose a smaller nozzle/layer profile.",
    )
    bounds = metrics.get("bounds_mm", [])
    bounds_ok = len(bounds) == 3 and all(0 < float(value) <= 300 for value in bounds)
    check(
        "Build volume",
        bounds_ok,
        " × ".join(f"{float(value):.1f}" for value in bounds) + " mm" if len(bounds) == 3 else "Missing bounds",
        "Scale or split the mesh to fit the 300 mm demonstration build volume.",
    )
    score = round(sum(bool(item["passed"]) for item in checks) / len(checks) * 100)
    status = "ready" if score == 100 else "review" if score >= 60 else "repair"
    return MeshAssessment(score=score, status=status, checks=checks, recommendations=recommendations)


def markdown(metrics: dict, result: MeshAssessment) -> str:
    rows = [
        "# Mesh print-readiness report",
        "",
        f"**Status:** `{result.status}`  ",
        f"**Score:** `{result.score}/100`",
        "",
        "| Check | Result | Detail |",
        "| --- | --- | --- |",
    ]
    rows.extend(
        f"| {item['name']} | {'PASS' if item['passed'] else 'REVIEW'} | {item['detail']} |"
        for item in result.checks
    )
    rows.extend(["", "## Recommendations", ""])
    rows.extend(f"- {value}" for value in result.recommendations or ["No blocking issues found in the supplied measurements."])
    rows.extend(["", "## Source metrics", "", "```json", json.dumps(metrics, indent=2), "```", ""])
    return "\n".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Assess sanitized mesh measurements")
    parser.add_argument("metrics", type=Path)
    parser.add_argument("--output", type=Path, default=Path("mesh-quality-report"))
    args = parser.parse_args()
    metrics = json.loads(args.metrics.read_text(encoding="utf-8"))
    result = assess(metrics)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".json").write_text(json.dumps(result.as_dict(), indent=2), encoding="utf-8")
    args.output.with_suffix(".md").write_text(markdown(metrics, result), encoding="utf-8")
    print(json.dumps(result.as_dict(), indent=2))
    return 0 if result.status != "repair" else 2


if __name__ == "__main__":
    raise SystemExit(main())
