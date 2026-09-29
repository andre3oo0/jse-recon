"""Rebuild the warehouse from every landed snapshot.

    python -m src.rebuild

Landing is the record; the warehouse is derived from it. The scheduled
GitHub Action commits snapshots to the `snapshots` branch and keeps no
database, so this is how a local warehouse catches up after pulling:

    git -C data/landing pull
    python -m src.rebuild
"""

import json
import sys

from src import config, db, ingest, security_master


def main() -> int:
    manifests = sorted(config.LANDING_DIR.glob(f"*/*/{ingest.MANIFEST_FILE}"))
    if not manifests:
        print(f"No snapshots under {config.LANDING_DIR}", file=sys.stderr)
        return 1

    config.DB_PATH.unlink(missing_ok=True)
    conn = db.connect()
    db.apply_schema(conn)
    security_master.sync(conn, [factory() for factory in ingest.SOURCES.values()])

    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        ingest.load(conn, path.parent / ingest.PRICES_FILE, manifest)
        print(f"loaded {manifest['source']} {manifest['snapshot_date']}: {manifest['rows']:,} rows")

    total = conn.execute("SELECT COUNT(*) FROM raw_price").fetchone()[0]
    print(f"\n{len(manifests)} snapshots, {total:,} raw rows in {config.DB_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
