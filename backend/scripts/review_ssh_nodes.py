"""Print a read-only inventory for reviewing SSH aliases before migration."""

import asyncio
import json

from sqlalchemy import select

from autoeval.catalog.standard_inputs import StandardInputCompilation
from autoeval.infrastructure.config import get_settings
from autoeval.infrastructure.db import create_database
from autoeval.resources.models import SSHResourceBinding
from autoeval.resources.node_inventory import planning_config


async def main():
    settings = get_settings()
    config = planning_config(settings)
    engine, sessions = create_database(settings)
    try:
        async with sessions() as session:
            bindings = (
                await session.scalars(
                    select(SSHResourceBinding).order_by(
                        SSHResourceBinding.host,
                        SSHResourceBinding.port,
                        SSHResourceBinding.created_at,
                    )
                )
            ).all()
            compilations = (
                await session.execute(
                    select(
                        StandardInputCompilation.id,
                        StandardInputCompilation.payload["runtime_config"]["resource_ids"],
                    )
                )
            ).all()
            rows = []
            for row in bindings:
                key = str(row.id)
                entries = config.get(key, {}).get("executors", {})
                rows.append(
                    {
                        "id": key,
                        "name": row.binding_key,
                        "host": row.host,
                        "port": row.port,
                        "username": row.username,
                        "workdir": row.workdir,
                        "fingerprint": row.fingerprint,
                        "auth_kind": row.auth_kind,
                        "templates": {
                            name: {
                                field: entry.get(field)
                                for field in ("revision", "image_id", "readonly_mounts")
                            }
                            for name, entry in entries.items()
                        },
                        "compilations": [
                            str(cid) for cid, ids in compilations if key in (ids or [])
                        ],
                    }
                )
            print(json.dumps(rows, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
