"""可选在线 badcase 检测：参数注入、监控投影与执行期增量采集。"""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from autoeval.evaluation.task_monitor import normalize, safe_task
from autoeval.planning.schemas import TaskInput
from autoeval_runtime.planning_runtime import OnlineBadcaseObserver


def task_payload():
    return {
        "task_id": "GPQA",
        "dataset": "GPQA",
        "compilation_id": str(uuid.uuid4()),
        "manifest_id": str(uuid.uuid4()),
    }


def test_task_input_hides_disabled_switch():
    body = task_payload()
    assert "online_badcase" not in TaskInput(**body).model_dump(mode="json")
    enabled = TaskInput(**body, online_badcase=True).model_dump(mode="json")
    assert enabled["online_badcase"] is True


def compiled(subcommand="llm_accuracy", params=None):
    return {
        "input_ready": True,
        "adaptor_revision": "955089ab32ef696c231d628f5ad0d9da28f430c4",
        "credential_binding": {"target_id": None},
        "config": {
            "tool": "ais_bench",
            "subcommand": subcommand,
            "params": params
            if params is not None
            else {"datasets": ["gpqa"], "batch_size": 1, "model_type": "vllm_api_general_chat"},
        },
        "locked_fields": {"params.batch_size": 1},
        "runtime_config": {},
    }


class Session:
    def __init__(self, payload):
        self.payload = payload

    async def get(self, model, key):
        name = model.__name__
        if name == "StandardInputCompilation":
            return SimpleNamespace(payload=self.payload, input_id=uuid.uuid4(), sha256="b" * 64)
        if name == "DatasetManifest":
            return SimpleNamespace(
                id=key,
                dataset_version_id=uuid.uuid4(),
                scope="full",
                sha256="c" * 64,
                sample_count=2,
            )
        if name == "AssetVersion":
            return SimpleNamespace(asset_id=uuid.uuid4())
        if name == "Asset":
            return SimpleNamespace(asset_key="GPQA")
        if name == "StandardInputVersion":
            return SimpleNamespace(
                benchmark="GPQA_Diamond",
                source_model="fixture",
                revision="r",
                repository="repo",
                payload={},
            )
        return None

    async def scalar(self, query):
        return SimpleNamespace(
            full_manifest_id=uuid.uuid4(),
            lite_manifest_id=uuid.uuid4(),
            sha256="d" * 64,
            contract={"family": "ais_bench", "revision": "r"},
        )


async def resolve(monkeypatch, payload, online_badcase):
    from fastapi import HTTPException

    from autoeval.planning import PlanInput, resolve_inputs

    body = PlanInput(
        target_id=str(uuid.uuid4()),
        resource_id=str(uuid.uuid4()),
        resource_kind="customer_ssh",
        tasks=[{**task_payload(), "online_badcase": online_badcase}],
    )

    payload["credential_binding"] = {"target_id": str(body.target_id)}

    async def target(*args):
        return {"model": "FIXTURE", "base_url": "https://fixture.invalid"}

    monkeypatch.setattr("autoeval.evaluation.inputs.target_data", target)
    monkeypatch.setattr(
        "autoeval.evaluation.inputs.read_manifest",
        lambda settings, manifest: {
            "samples": [{"sample_id": "s0", "sha256": "1"}, {"sample_id": "s1", "sha256": "2"}]
        },
    )
    monkeypatch.setattr(
        "autoeval.planning.planning_transport.select_execution_template",
        lambda *args, **kwargs: (str(body.resource_id), {}),
    )
    try:
        return await resolve_inputs(
            Session(payload), SimpleNamespace(archived_manifest_ids=[]), body
        )
    except HTTPException as exc:
        return exc


async def test_resolve_inputs_injects_tag_only_detector(monkeypatch):
    inputs = await resolve(monkeypatch, compiled(), True)
    params = inputs["tasks"][0]["compilation"]["config"]["params"]
    assert params["generation_kwargs"]["online_badcase"] == {"abort": False}
    assert params["dump_eval_details"] is True
    assert inputs["tasks"][0]["online_badcase"] is True


async def test_resolve_inputs_keeps_detection_off_by_default(monkeypatch):
    inputs = await resolve(monkeypatch, compiled(), False)
    params = inputs["tasks"][0]["compilation"]["config"]["params"]
    assert "generation_kwargs" not in params


async def test_resolve_inputs_rejects_agent_dataset(monkeypatch):
    from fastapi import HTTPException

    exc = await resolve(monkeypatch, compiled("harbor_agent"), True)
    assert isinstance(exc, HTTPException) and exc.status_code == 422


def observation(hits=2):
    return {
        "enabled": True,
        "hits": hits,
        "aborted": -1,
        "max_out_len_hits": 1,
        "wasted_chars": 128,
        "wasted_ms": 456,
        "details": [
            {
                "case_id": "c" * 100,
                "reason": "short_cycle",
                "detected_at_chars": 200,
                "total_chars": 320,
                "wasted_ms": 10,
                "aborted": True,
                "max_out_len_hit": "not-a-bool",
                "prediction": "secret model text",
            }
        ]
        * 30,
    }


def test_monitor_projection_bounds_online_badcase():
    stamp = datetime.now(UTC).isoformat()
    task = {
        "task_name": "fixture",
        "status": "running",
        "observed_at": stamp,
        "online_badcase": observation(),
    }
    projection = normalize(task, {"task_name": "fixture"}, "aisbench_native_predictions", stamp)
    value = projection["online_badcase"]
    assert value["enabled"] is True and value["hits"] == 2
    assert value["aborted"] is None and value["max_out_len_hits"] == 1
    assert len(value["details"]) == 20
    detail = value["details"][0]
    assert len(detail["case_id"]) == 64
    assert detail["max_out_len_hit"] is None
    assert "prediction" not in detail
    archived = safe_task(task)["online_badcase"]
    assert archived == value


def test_monitor_projection_ignores_invalid_payload():
    stamp = datetime.now(UTC).isoformat()
    task = {"task_name": "fixture", "status": "running", "online_badcase": "raw"}
    projection = normalize(task, {"task_name": "fixture"}, "aisbench_native_predictions", stamp)
    assert projection["online_badcase"] is None


async def test_publish_forwards_online_badcase(monkeypatch):
    from autoeval.planning import planning_observations

    captured = {}

    async def save(sessions, settings, binding_id, task, source, observed_at, kind="live"):
        captured.update(task)

    class SessionContext:
        async def __aenter__(self):
            class Inner:
                async def scalar(self, query):
                    return SimpleNamespace(id=uuid.uuid4(), latest={"finish_count": 1})

            return Inner()

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(planning_observations, "save_observation", save)
    child = uuid.uuid4()
    progress = {
        "source": "aisbench_native_predictions",
        "job_id": str(child),
        "finish_count": 2,
        "total_count": 4,
        "observed_at": datetime.now(UTC).isoformat(),
        "online_badcase": observation(3),
    }
    await planning_observations.publish(
        SessionContext, SimpleNamespace(), uuid.uuid4(), child, progress
    )
    assert captured["online_badcase"]["hits"] == 3


def write_records(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_observer_reads_incrementally_and_dedupes(tmp_path):
    output = tmp_path / "output"
    predictions = output / "20261123_080000" / "predictions" / "fixture" / "gpqa.jsonl"
    write_records(
        predictions,
        [
            {"id": "0", "online_badcase": {"reason": "short_cycle", "detected_at_chars": 200, "total_chars": 320, "wasted_ms": 10}},
            {"id": "1", "success": True},
        ],
    )
    observer = OnlineBadcaseObserver(output, "fixture")
    first = observer.poll()
    assert first["hits"] == 1 and first["wasted_chars"] == 120 and first["wasted_ms"] == 10
    write_records(
        predictions,
        [
            {"id": "0", "online_badcase": {"reason": "short_cycle", "detected_at_chars": 200, "total_chars": 320, "wasted_ms": 10}},
            {"id": "2", "online_badcase": {"reason": "long_segment", "repeat_chars": 64, "max_out_len_hit": True, "aborted": False}},
        ],
    )
    second = observer.poll()
    assert second["hits"] == 2 and second["max_out_len_hits"] == 1
    assert second["wasted_chars"] == 184
    assert [row["case_id"] for row in second["details"]] == ["0", "2"]


def test_observer_bounds_details_and_resets_rewritten_file(tmp_path):
    output = tmp_path / "output"
    predictions = output / "20261123_080000" / "predictions" / "fixture" / "gpqa.jsonl"
    write_records(
        predictions,
        [{"id": str(i), "online_badcase": {"reason": "short_cycle"}} for i in range(25)],
    )
    observer = OnlineBadcaseObserver(output, "fixture")
    value = observer.poll()
    assert value["hits"] == 25 and len(value["details"]) == 20
    # A rewritten file must not keep stale offsets or duplicate counters.
    predictions.write_text(
        json.dumps({"id": "0", "online_badcase": {"reason": "short_cycle"}}) + "\n",
        encoding="utf-8",
    )
    reset = observer.poll()
    assert reset["hits"] == 1 and [row["case_id"] for row in reset["details"]] == ["0"]


def test_observer_is_noop_without_predictions(tmp_path):
    value = OnlineBadcaseObserver(tmp_path / "output", "fixture").poll()
    assert value == {
        "enabled": True,
        "hits": 0,
        "aborted": 0,
        "max_out_len_hits": 0,
        "wasted_chars": 0,
        "wasted_ms": 0,
        "details": [],
    }


def test_observer_tolerates_partial_records(tmp_path):
    output = tmp_path / "output"
    predictions = output / "predictions" / "fixture" / "gpqa.jsonl"
    predictions.parent.mkdir(parents=True)
    predictions.write_text('{"id": "0", "online_badcase": {"reason": "short', encoding="utf-8")
    observer = OnlineBadcaseObserver(output, "fixture")
    assert observer.poll()["hits"] == 0
    with predictions.open("a", encoding="utf-8") as handle:
        handle.write('_cycle"}}\n')
    assert observer.poll()["hits"] == 1


@pytest.mark.parametrize("value", [None, [], {"enabled": True}])
def test_monitor_projection_handles_missing_counters(value):
    stamp = datetime.now(UTC).isoformat()
    task = {"task_name": "fixture", "status": "running", "online_badcase": value}
    projection = normalize(task, {"task_name": "fixture"}, "aisbench_native_predictions", stamp)
    assert projection["online_badcase"] is None or projection["online_badcase"]["hits"] is None
