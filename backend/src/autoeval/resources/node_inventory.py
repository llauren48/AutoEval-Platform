"""Operator-owned image and per-node dataset registrations."""

import json
import os
import uuid
from pathlib import Path, PurePosixPath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from autoeval.resources.models import SSHResourceBinding

Revision = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class SharedImage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1, max_length=128)
    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    aisbench_revision: Revision
    harbor_revision: Revision
    tool_root: str = Field(pattern=r"^/[^,]+$")

    @model_validator(mode="after")
    def absolute_roots(self):
        path = PurePosixPath(self.tool_root)
        if ".." in path.parts or str(path) != self.tool_root or self.tool_root.startswith("//"):
            raise ValueError("Invalid image source directory")
        return self


def planning_config(settings):
    path = os.environ.get("AE_PLANNING_CONTAINER_CONFIG") or getattr(
        settings, "planning_container_config", None
    )
    return json.loads(Path(path).read_text(encoding="utf-8-sig")) if path else {}


def shared_node(config, resource_id):
    node = config.get(str(resource_id), {})
    if "datasets" not in node:
        return None
    return SharedImage.model_validate(config["aisbench_runtime"]), node


def login_identity(row):
    return (row.host.lower().rstrip("."), row.port, row.username, row.fingerprint, row.auth_kind)


async def checked_aliases(session, config):
    """A declaration attests equivalent permissions and approved work-directory differences."""
    aliases = {}
    for key, node in config.items():
        if key == "aisbench_runtime" or "datasets" not in node:
            continue
        for old in node.get("legacy_ids", []):
            if old in aliases or old == key or "datasets" in config.get(old, {}):
                raise ValueError("Duplicate or chained SSH node alias")
            aliases[old] = key
    if aliases:
        ids = set(aliases) | set(aliases.values())
        rows = {
            str(row.id): row
            for row in (
                await session.scalars(
                    select(SSHResourceBinding).where(
                        SSHResourceBinding.id.in_([uuid.UUID(i) for i in ids])
                    )
                )
            ).all()
        }
        if ids != rows.keys() or any(
            login_identity(rows[old]) != login_identity(rows[new]) for old, new in aliases.items()
        ):
            raise ValueError("Incompatible SSH node alias")
    return aliases
