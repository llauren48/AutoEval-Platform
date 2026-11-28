import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from autoeval.planning.planning_transport import execution_template, select_execution_template
from autoeval.resources.node_inventory import checked_aliases
from autoeval_runtime.planning_runtime import check_accuracy_mounts, check_task_selection


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    image = {
        "version": "pilot-1",
        "image_id": "sha256:" + "a" * 64,
        "tool_root": "/benchmark",
        "aisbench_revision": "b" * 40,
        "harbor_revision": "c" * 40,
    }
    node = str(uuid4())
    config = {
        "aisbench_runtime": image,
        node: {
            "datasets": {
                d: [{"source": "/mnt/" + d, "target": "/data/" + d}]
                for d in ("GPQA", "MMMU-Pro", "DeepSWE", "AutomationBench")
            },
            "harbor": {"private_docker": True},
            "executors": {"aisbench": {"revision": "b" * 40, "image_id": "sha256:" + "d" * 64}},
        },
    }
    path = tmp_path / "containers.json"
    path.write_text(json.dumps(config))
    monkeypatch.setenv("AE_PLANNING_CONTAINER_CONFIG", str(path))
    return node, config, path


def task_for(dataset):
    mode = "harbor_agent" if dataset in {"DeepSWE", "AutomationBench"} else "llm_accuracy"
    return {
        "dataset": dataset,
        "tool": {"revision": "b" * 40},
        "compilation": {
            "config": {
                "subcommand": mode,
                "params": {"ais_bench_root": "/benchmark", "path": "/data/" + dataset},
            },
            "runtime_config": {"datasets_dir": "/data/" + dataset},
        },
    }


@pytest.mark.parametrize("dataset", ["GPQA", "MMMU-Pro", "DeepSWE", "AutomationBench"])
def test_one_image_with_only_selected_mounts(inventory, dataset):
    node, config, _ = inventory
    task = task_for(dataset)
    _, template = select_execution_template(None, [node], None, task)
    assert template["image_id"] == config["aisbench_runtime"]["image_id"]
    assert template["readonly_mounts"] == config[node]["datasets"][dataset]
    assert execution_template(None, node, {**task, "execution_template": template}) == template


def test_missing_mount_wrong_revision_and_frozen_legacy(inventory):
    node, config, path = inventory
    task = task_for("GPQA")
    frozen = execution_template(None, node, task)
    assert (
        execution_template(None, node, {**task, "execution_template": {"family": "aisbench"}})[
            "image_id"
        ]
        == "sha256:" + "d" * 64
    )
    assert execution_template(None, node, {**task, "tool": {"revision": "e" * 40}}) is None
    del config[node]["datasets"]["GPQA"]
    path.write_text(json.dumps(config))
    assert execution_template(None, node, task) is None
    assert execution_template(None, node, {**task, "execution_template": frozen}) is None


@pytest.mark.asyncio
async def test_aliases_require_matching_ssh_login(inventory):
    node, config, _ = inventory
    old = str(uuid4())
    config[node]["legacy_ids"] = [old]
    rows = [
        SimpleNamespace(
            id=i,
            host="node",
            port=22,
            username="operator",
            fingerprint="one",
            auth_kind="private_key",
        )
        for i in (old, node)
    ]
    session = SimpleNamespace(scalars=AsyncMock(return_value=Mock(all=lambda: rows)))
    assert await checked_aliases(session, config) == {old: node}
    rows[0].username = "another-user"
    with pytest.raises(ValueError, match="Incompatible SSH"):
        await checked_aliases(session, config)


def test_frozen_task_uses_explicitly_retained_runtime(inventory):
    node, config, path = inventory
    task = task_for("GPQA")
    original = execution_template(None, node, task)
    frozen = {**task, "execution_template": original}
    previous = json.loads(path.read_text())
    config["aisbench_runtime"].update(version="v2", image_id="sha256:" + "e" * 64)
    path.write_text(json.dumps(config))
    assert execution_template(None, node, frozen) is None
    config["retained_aisbench"] = {"pilot-1": previous}
    path.write_text(json.dumps(config))
    assert execution_template(None, node, frozen) == original
    assert execution_template(None, node, task)["image_id"] != original["image_id"]


def test_harbor_selection_uses_only_registered_mounts(tmp_path, monkeypatch):
    package = tmp_path / "harbor" / "models" / "job"
    package.mkdir(parents=True)
    (package / "config.py").write_text(
        "from types import SimpleNamespace\n"
        "class DatasetConfig:\n"
        "    def __init__(self, path, task_names):\n"
        "        self.tasks = [SimpleNamespace(path=path/name) for name in task_names]\n"
        "    async def get_task_configs(self, disable_verification):\n"
        "        return self.tasks\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    mounted = tmp_path / "registered"
    mounted.mkdir()
    (tmp_path / "unregistered").mkdir()
    config = {"params": {"path": str(tmp_path), "task_names": ["registered"]}}
    assert check_task_selection(config, [str(mounted)])
    config["params"]["task_names"].append("unregistered")
    assert not check_task_selection(config, [str(mounted)])


def test_accuracy_checks_actual_file_and_selected_range(tmp_path, monkeypatch):
    config = SimpleNamespace(module="dataset_config", variable="rows")
    monkeypatch.setitem(
        sys.modules,
        "autoeval_adaptor.discovery",
        SimpleNamespace(resolve_dataset=lambda root, name: config),
    )
    monkeypatch.setitem(
        sys.modules, "autoeval_adaptor.errors", SimpleNamespace(DiscoveryError=ValueError)
    )
    utils = tmp_path / "ais_bench" / "benchmark" / "datasets" / "utils"
    utils.mkdir(parents=True)
    (utils / "datasets.py").write_text("def get_data_path(path): return path\n")
    mounted = tmp_path / "read-only-data"
    mounted.mkdir()
    (mounted / "cases.csv").write_text("question,answer\none,yes\ntwo,no\n")
    (tmp_path / "dataset_config.py").write_text(
        "rows = " + repr([{"path": str(mounted), "name": "cases.csv"}])
    )
    params = {
        "ais_bench_root": str(tmp_path),
        "datasets": ["sample"],
        "dataset_test_range": "[1:2]",
    }
    assert check_accuracy_mounts(params, [str(mounted)])
    assert not check_accuracy_mounts(params, [str(utils)])
    assert not check_accuracy_mounts({**params, "dataset_test_range": "[1:3]"}, [str(mounted)])
