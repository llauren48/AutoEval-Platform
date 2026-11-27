"""Exercise the container probe against native-format result files."""

import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from autoeval.planning import planning_observations, planning_transport
from autoeval.planning.planning_observations import harbor_dispatch_contract


@pytest.mark.parametrize(("total", "finished"), [(5, 4), (5, 0), (5, 5), (1, 0), (1, 1)])
async def test_harbor_sample_progress(tmp_path, monkeypatch, total, finished):
    job = SimpleNamespace(submission_key=str(uuid4()))
    root = tmp_path / job.submission_key
    root.mkdir()
    monkeypatch.setattr(planning_transport, "ARTIFACT_ROOT", str(tmp_path))

    def command(args):
        return subprocess.check_output([sys.executable, *args[3:]], text=True)

    adapter = SimpleNamespace(command=command, container_id="test", debug_storage=True)
    samples = [{"tool_case_id": f"case-{i}", "sample_id": str(i)} for i in range(total)]
    contract = harbor_dispatch_contract({"dataset": "DeepSWE", "samples": samples})
    spec = {
        "compilation": {"config": {"subcommand": "harbor_agent", "params": {}}},
        "harbor_dispatch_contract": contract,
    }
    (root / "spec.json").write_text(json.dumps(spec))
    (root / "progress.json").write_text(json.dumps({"job_id": root.name, "stage": "tool_running"}))

    def write_trial(directory, task_name):
        trial = root / "output" / directory
        trial.mkdir(parents=True)
        (trial / "result.json").write_text(
            json.dumps(
                {
                    "task_name": task_name,
                    "finished_at": "2026-11-27T00:00:00Z",
                    "agent_execution": {},
                    "verifier_result": {},
                }
            )
        )

    for i in range(finished):
        write_trial(f"trial-{i}", f"datacurve/case-{i}")
    progress = await planning_transport.read_container_progress(adapter, job)
    assert (progress["finish_count"], progress.get("total_count")) == (finished, total)

    if finished:
        write_trial("retry", "case-0")
        write_trial("foreign", "other-case")
        progress = await planning_transport.read_container_progress(adapter, job)
        assert (progress["finish_count"], progress["total_count"]) == (finished, total)


@pytest.mark.parametrize(
    ("source", "expected"), [("harbor_result_files", 5), ("aisbench_performance", None)]
)
async def test_publish_partial_snapshot(monkeypatch, source, expected):
    child = SimpleNamespace(id=uuid4(), payload={"samples": [{}] * 5})
    binding = SimpleNamespace(id=uuid4(), latest={"total_count": None})
    sessions = MagicMock()
    sessions.return_value.__aenter__.return_value.scalar = AsyncMock(return_value=binding)
    save = AsyncMock()
    monkeypatch.setattr(planning_observations, "save_observation", save)
    for finished in (0, 4, 5):
        progress = {
            "job_id": str(child.id),
            "source": source,
            "observed_at": "2026-11-27T00:00:00Z",
        }
        if source == "harbor_result_files":
            progress["finish_count"] = finished
        summary = await planning_observations.publish(sessions, None, uuid4(), child, progress)
        binding.latest = save.await_args.args[3]
        assert binding.latest["total_count"] == summary.get("total_count") == expected
