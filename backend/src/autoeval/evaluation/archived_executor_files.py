"""Read native file attachments from checksum-verified, job-scoped archives."""

import asyncio

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select

from autoeval.catalog.datasets import read_manifest
from autoeval.evaluation.models import ExecutionShard
from autoeval.reporting.harbor_trace import FILES
from autoeval.reporting.models import UnifiedArtifact, UnifiedCollection
from autoeval_runtime.debug_files import LIMIT, permitted, text_preview


def trial_files(trials):
    for trial in trials:
        if not trial or not permitted(trial.get("trial_name", "")):
            continue
        for path, content in trial.get("files", {}).items():
            if path in FILES:
                yield f"{trial['trial_name']}/{path}", content


def archive_directory(files, path, offset, metadata=None):
    prefix = path + "/" if path else ""
    entries = {}
    for filename, info in (
        metadata or {k: {"size": len(v.encode()), "mtime": t} for k, (v, t) in files.items()}
    ).items():
        timestamp = info["mtime"]
        if not filename.startswith(prefix):
            continue
        name, separator, _ = filename[len(prefix) :].partition("/")
        if separator and name in entries:
            entries[name]["mtime"] = max(entries[name]["mtime"], timestamp)
            continue
        entries[name] = {
            "name": name,
            "directory": bool(separator),
            "size": 0 if separator else info["size"],
            "mtime": timestamp,
            **({"metadata_only": True} if not separator and info.get("metadata_only") else {}),
        }
    if path and not entries:
        return {"state": "missing_file"}
    children = sorted(entries.values(), key=lambda entry: (not entry["directory"], entry["name"]))
    return {
        "state": "directory",
        "archived": True,
        "entries": children[offset : offset + 100],
        "more": offset + 100 < len(children),
    }


async def archived_files(session, settings, run_id, job, path, offset, *, directory=False):
    collection_key = f"round-shard:{job.id}" if isinstance(job, ExecutionShard) else job.task_id
    collection = await session.scalar(
        select(UnifiedCollection).where(
            UnifiedCollection.run_id == run_id, UnifiedCollection.collection_key == collection_key
        )
    )
    keys = {f"planning-harbor:{job.id}", f"planning-native:{job.id}", f"harbor-details:{job.id}"}
    bundles = await session.scalars(
        select(UnifiedArtifact).where(
            UnifiedArtifact.run_id == run_id, UnifiedArtifact.collection_key.in_(keys)
        )
    )
    files = {}
    metadata = {}

    def add(entries, archived_at):
        for name, content in entries:
            if not isinstance(content, str) or not all(permitted(part) for part in name.split("/")):
                continue
            if name in files and files[name][0] != content:
                raise ValueError("Conflicting native file contents")
            files[name] = content, archived_at.timestamp()

    try:
        for row in bundles:
            bundle = await asyncio.to_thread(read_manifest, settings, row)
            if bundle["child_id"] != str(job.id) or bundle["run_id"] != str(run_id):
                raise ValueError("Native archive belongs to another job")
            if row.collection_key == f"harbor-details:{job.id}":
                for item in bundle["files"]:
                    name = item["path"]
                    parts = name.split("/")
                    if "details" not in parts[:-1] or any(
                        p in {"", ".", ".."} or chr(92) in p for p in parts
                    ):
                        continue
                    public = isinstance(item.get("content"), str) and all(
                        permitted(p) for p in parts
                    )
                    metadata[name] = {
                        "size": item["size"],
                        "mtime": row.created_at.timestamp(),
                        "metadata_only": not public,
                    }
                    if public:
                        add([(name, item["content"])], row.created_at)
            elif row.collection_key == f"planning-native:{job.id}":
                add(((f["path"], f["content"]) for f in bundle["files"]), row.created_at)
            else:
                add(trial_files(bundle["trials"]), row.created_at)
        if collection and job.payload["compilation"]["config"]["subcommand"] == "harbor_agent":
            result = await asyncio.to_thread(read_manifest, settings, collection)
            add(
                trial_files(c.get("harbor_artifact") for c in result["cases"]),
                collection.created_at,
            )
    except (BotoCoreError, ClientError, ValueError, KeyError, TypeError, OSError):
        return {"state": "unavailable"}
    metadata = {
        **{k: {"size": len(v.encode()), "mtime": t} for k, (v, t) in files.items()},
        **metadata,
    }
    if directory:
        return archive_directory(files, path, offset, metadata) if metadata else None
    if path:
        if metadata.get(path, {}).get("metadata_only"):
            return {"state": "protected", "size": metadata[path]["size"]}
        if path not in files:
            return {"state": "missing_file"}
        raw = files[path][0].encode()
        content = raw[:LIMIT].decode("utf-8", errors="ignore")
        return text_preview(content, 0, len(content.encode()), len(raw))
    if not metadata:
        return None
    names = sorted(metadata)
    return {
        "state": "directory",
        "archived": True,
        "entries": [
            {
                "name": name,
                "directory": False,
                **metadata[name],
            }
            for name in names[offset : offset + 100]
        ],
        "more": offset + 100 < len(names),
    }
