import json
import uuid
from types import SimpleNamespace

import pytest

from autoeval.infrastructure.config import Settings
from autoeval.reporting.langfuse_integration import build_plan, import_one, trace_view


def artifact():
    return {
        "trial_name": "task__agent__model__1",
        "files": {
            "agent/trajectory.json": json.dumps(
                {
                    "session_id": "session",
                    "steps": [
                        {
                            "step_id": 1,
                            "source": "user",
                            "message": "question",
                            "timestamp": "2026-11-16T00:00:00Z",
                        },
                        {
                            "step_id": 2,
                            "source": "agent",
                            "message": "answer",
                            "model_name": "model",
                            "timestamp": "2026-11-16T00:00:01Z",
                        },
                    ],
                }
            ),
            "verifier/reward.txt": "1",
        },
    }


def test_plan_identity_and_score(tmp_path):
    rec, first = build_plan(artifact(), tmp_path / "one", "attempt-1")
    _, second = build_plan(artifact(), tmp_path / "two", "attempt-1")
    _, different = build_plan(artifact(), tmp_path / "three", "attempt-2")
    assert rec.reward == 1
    assert first[0].trace_id == second[0].trace_id
    assert first[0].trace_id != different[0].trace_id
    assert any(s.role == "generation" for s in first)


def test_full_content_is_preserved_with_separate_stable_identity(tmp_path):
    data = artifact()
    trajectory = json.loads(data["files"]["agent/trajectory.json"])
    trajectory["steps"][1]["reasoning_content"] = "fixture reasoning"
    data["files"]["agent/trajectory.json"] = json.dumps(trajectory)
    _, structural = build_plan(data, tmp_path / "structural", "same-attempt")
    _, full = build_plan(data, tmp_path / "full", "same-attempt", mode="full")
    _, repeated = build_plan(data, tmp_path / "repeat", "same-attempt", mode="full")
    generation = next(span for span in full if span.role == "generation")
    assert generation.attributes["langfuse.observation.output"] == "answer"
    assert generation.attributes["atif.reasoning"] == "fixture reasoning"
    assert structural[0].trace_id != full[0].trace_id == repeated[0].trace_id
    assert isinstance(
        next(s for s in structural if s.role == "generation").attributes[
            "langfuse.observation.output"
        ],
        dict,
    )
    with pytest.raises(ValueError, match="content mode"):
        build_plan(data, tmp_path / "invalid", "same-attempt", mode="invalid")


@pytest.mark.asyncio
async def test_new_import_freezes_full_mode_before_configuration_changes(monkeypatch):
    from autoeval.reporting.langfuse_integration import archive_trace

    settings = Settings(
        langfuse_host="https://internal.example",
        langfuse_browser_url="https://browser.example",
        langfuse_project_id="project",
        langfuse_public_key="public",
        langfuse_secret_key="secret",
        _env_file=None,
    )
    assert settings.langfuse_content_mode == "full"
    saved = []
    session = SimpleNamespace(add=saved.append)
    monkeypatch.setattr(
        "autoeval.reporting.langfuse_integration.write_manifest", lambda *args: None
    )
    await archive_trace(session, settings, uuid.uuid4(), uuid.uuid4(), artifact())
    settings.langfuse_content_mode = "structural"
    assert saved[0].content_mode == "full"


def test_artifact_path_rejected(tmp_path):
    data = artifact()
    data["files"]["../secret"] = "bad"
    with pytest.raises(ValueError):
        build_plan(data, tmp_path, "attempt")


def test_link_requires_confirmation():
    settings = Settings(langfuse_browser_url="https://langfuse.example", _env_file=None)
    row = SimpleNamespace(
        status="failed", langfuse_project="project", trace_id="a" * 32, error_code="FAILED"
    )
    assert trace_view(row, settings)["trace_url"] is None
    row.status = "succeeded"
    assert trace_view(row, settings)["trace_url"].endswith("/project/project/traces/" + "a" * 32)
    assert trace_view(None, settings)["trace_status"] == "unsupported"


def test_import_configuration_requires_all_backend_fields():
    from autoeval.reporting.langfuse_integration import import_configured

    fields = {
        "langfuse_project_id": "project",
        "langfuse_host": "https://internal.example",
        "langfuse_browser_url": "https://browser.example",
        "langfuse_public_key": "public",
        "langfuse_secret_key": "secret",
    }
    assert import_configured(Settings(**fields, _env_file=None))
    for name in fields:
        assert not import_configured(Settings(**{**fields, name: ""}, _env_file=None))


@pytest.mark.asyncio
async def test_query_failure_never_sends(monkeypatch):
    import httpx

    from autoeval.reporting import langfuse_integration

    sent = []
    monkeypatch.setattr(langfuse_integration, "send_spans", lambda *args: sent.append(True))

    async def failure(*args, **kwargs):
        raise httpx.ConnectError("unavailable")

    monkeypatch.setattr(httpx.AsyncClient, "get", failure)
    settings = Settings(
        langfuse_host="https://langfuse.example",
        langfuse_browser_url="https://langfuse.example",
        _env_file=None,
    )
    with pytest.raises(httpx.ConnectError):
        await import_one(SimpleNamespace(attempt_id=uuid.uuid4()), settings, artifact())
    assert not sent


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["complete", "partial", "unauthorized"])
async def test_import_confirmation_and_no_duplicate_send(monkeypatch, tmp_path, outcome):
    import httpx

    from autoeval.reporting import langfuse_integration

    row = SimpleNamespace(attempt_id=uuid.uuid4())
    _, plan = build_plan(artifact(), tmp_path, str(row.attempt_id))
    ids = [format(spec.span_id, "016x") for spec in plan]
    sent = []
    monkeypatch.setattr(langfuse_integration, "send_spans", lambda *args: sent.append(True))

    async def accept_score(client, url, **kwargs):
        return httpx.Response(201, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", accept_score)

    async def response(*args, **kwargs):
        return httpx.Response(
            401 if outcome == "unauthorized" else 200,
            request=httpx.Request("GET", "https://langfuse.example/api/public/v2/observations"),
            json={"data": [{"id": value} for value in (ids if outcome == "complete" else ids[:1])]},
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", response)
    settings = Settings(
        langfuse_host="https://langfuse.example",
        langfuse_browser_url="https://langfuse.example",
        _env_file=None,
    )
    if outcome == "complete":
        assert await import_one(row, settings, artifact()) == format(plan[0].trace_id, "032x")
    else:
        with pytest.raises((RuntimeError, httpx.HTTPStatusError)):
            await import_one(row, settings, artifact())
    assert not sent


def test_fixed_automation_tool_tree_and_inferred_timing(tmp_path):
    from pathlib import Path

    data = json.loads(
        (Path(__file__).parent / "fixtures/langfuse/automationbench-artifact.json").read_text()
    )
    rec, plan = build_plan(data, tmp_path, "fixture-attempt")
    tools = [s for s in plan if s.role == "tool"]
    assert len(tools) == 1 and tools[0].name == "tool.read_record"
    assert plan[tools[0].parent].role == "generation"
    assert rec.reward == 1
    assert tools[0].attributes["atif.timing_source"].startswith("inferred")


@pytest.mark.asyncio
async def test_import_retries_scores_directly_under_container_proxy(monkeypatch, tmp_path):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    from urllib.parse import urlsplit

    from harborstack.convert.scores import score_id
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    for name in ("http_proxy", "https_proxy", "all_proxy"):
        for spelling in (name, name.upper()):
            monkeypatch.setenv(spelling, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "other.invalid")

    data = artifact()
    del data["files"]["verifier/reward.txt"]
    data["files"]["verifier/reward.json"] = json.dumps({"reward": 1, "quality": 0.5})
    received_spans = []
    scores = []

    class LangfuseHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert urlsplit(self.path).path == "/api/public/v2/observations"
            self.send_response(200)
            self.end_headers()
            data = [{"id": span.span_id.hex()} for span in received_spans]
            self.wfile.write(json.dumps({"data": data}).encode())

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/api/public/otel/v1/traces":
                assert self.headers["x-langfuse-ingestion-version"] == "4"
                batch = ExportTraceServiceRequest.FromString(body)
                for resource in batch.resource_spans:
                    for scope in resource.scope_spans:
                        received_spans.extend(scope.spans)
                self.send_response(200)
            else:
                assert self.path == "/api/public/scores"
                scores.append(json.loads(body))
                self.send_response(503 if len(scores) == 1 else 201)
            self.end_headers()

    row = SimpleNamespace(attempt_id=uuid.uuid4())
    _, plan = build_plan(data, tmp_path, str(row.attempt_id))
    trace_id = format(plan[0].trace_id, "032x")
    with HTTPServer(("127.0.0.1", 0), LangfuseHandler) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            host = f"http://127.0.0.1:{server.server_port}"
            settings = Settings(
                langfuse_host=host,
                langfuse_browser_url=host,
                langfuse_public_key="public",
                langfuse_secret_key="secret",
                _env_file=None,
            )
            with pytest.raises(RuntimeError, match="Score ingestion not confirmed"):
                await import_one(row, settings, data)
            first_scores = scores.copy()
            assert "reward.quality" in [item["name"] for item in first_scores]
            assert await import_one(row, settings, data) == trace_id
        finally:
            server.shutdown()
            thread.join()

    assert sorted(span.span_id.hex() for span in received_spans) == sorted(
        format(spec.span_id, "016x") for spec in plan
    )
    assert all(span.trace_id.hex() == trace_id for span in received_spans)
    assert scores == first_scores * 2
    assert scores[0] == {
        "name": "reward",
        "value": 1.0,
        "dataType": "NUMERIC",
        "id": score_id(trace_id, "reward"),
        "traceId": trace_id,
        "observationId": format(plan[0].span_id, "016x"),
        "timestamp": plan[0].start_ts,
    }
