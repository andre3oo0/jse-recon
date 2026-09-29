"""Rebuild the warehouse from every landed snapshot; landing is the record and the warehouse is derived."""

import json
import sys

from src import config, db, holdings, ingest, security_master, staging


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

    staged = staging.build(conn)
    if holdings.EXPORT.exists():
        holdings.run(conn)
    print(f"\n{len(manifests)} snapshots, {staged:,} staged rows in {config.DB_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
