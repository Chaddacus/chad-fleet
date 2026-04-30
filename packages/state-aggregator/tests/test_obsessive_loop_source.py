"""Tests for ObsessiveLoopSource using tmp_path."""

import json
from pathlib import Path

from state_aggregator.sources import ObsessiveLoopSource


def _make_run(runs_root: Path, run_id: str, summary: dict | None = None) -> Path:
    """Create a fake run directory."""
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)
    if summary is not None:
        (run_dir / "summary.json").write_text(json.dumps(summary))
    return run_dir


def test_obsessive_loop_source_empty_root(tmp_path):
    src = ObsessiveLoopSource(state_root=tmp_path / "nonexistent")
    result = src.fetch()
    assert result == {"runs": []}


def test_obsessive_loop_source_reads_summary_json(tmp_path):
    runs_root = tmp_path / "obsessive-loop"
    runs_root.mkdir()

    _make_run(
        runs_root,
        "run-001",
        {"run_id": "run-001", "status": "complete", "repo_path": "/repos/alpha"},
    )
    _make_run(
        runs_root,
        "run-002",
        {"run_id": "run-002", "status": "in_progress", "repo_path": "/repos/beta"},
    )

    src = ObsessiveLoopSource(state_root=runs_root)
    result = src.fetch()

    assert "runs" in result
    assert len(result["runs"]) == 2
    run_ids = {r["run_id"] for r in result["runs"]}
    assert "run-001" in run_ids
    assert "run-002" in run_ids


def test_obsessive_loop_source_run_without_summary(tmp_path):
    runs_root = tmp_path / "obsessive-loop"
    runs_root.mkdir()

    # Run dir with no summary.json or state.jsonl — minimal stub
    run_dir = runs_root / "run-bare"
    run_dir.mkdir()

    src = ObsessiveLoopSource(state_root=runs_root)
    result = src.fetch()
    assert len(result["runs"]) == 1
    assert result["runs"][0]["run_id"] == "run-bare"


def test_obsessive_loop_source_reads_state_jsonl(tmp_path):
    runs_root = tmp_path / "obsessive-loop"
    runs_root.mkdir()

    run_dir = runs_root / "run-jsonl"
    run_dir.mkdir()
    events = [
        json.dumps({"state": "running", "repo_path": "/repos/gamma", "branch": "main"}),
        json.dumps({"state": "complete", "repo_path": "/repos/gamma", "branch": "main"}),
    ]
    (run_dir / "state.jsonl").write_text("\n".join(events))

    src = ObsessiveLoopSource(state_root=runs_root)
    result = src.fetch()
    assert len(result["runs"]) == 1
    run = result["runs"][0]
    assert run["run_id"] == "run-jsonl"
    assert run["status"] == "complete"


def test_obsessive_loop_source_baseline_attached(tmp_path):
    runs_root = tmp_path / "obsessive-loop"
    runs_root.mkdir()

    run_dir = runs_root / "run-with-baseline"
    run_dir.mkdir()
    baseline = {"score": 87, "grade": "B+"}
    (run_dir / "baseline-scorecard.json").write_text(json.dumps(baseline))

    src = ObsessiveLoopSource(state_root=runs_root)
    result = src.fetch()
    run = result["runs"][0]
    assert run["baseline"] == baseline
