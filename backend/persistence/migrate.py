"""Explicit, transactional migration of the original single-dietitian database."""

from argparse import ArgumentParser

from backend.config.settings import settings
from backend.persistence.database import Database
from backend.persistence.schema import apply_schema
from backend.tenancy import load_tenant


def main(argv=None) -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--legacy-dietitian-id",
        help="Assign previously unowned recipes to this configured ID. Stop old servers first.",
    )
    args = parser.parse_args(argv)
    legacy = load_tenant(args.legacy_dietitian_id) if args.legacy_dietitian_id else None
    db = Database(
        host=settings.DB_HOST, port=settings.DB_PORT, dbname=settings.DB_NAME,
        user=settings.DB_USER, password=settings.DB_PASSWORD,
    )
    try:
        apply_schema(db, legacy_profile=legacy.profile if legacy else None)
    finally:
        db.close()
    print("Tenant recipe schema is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
