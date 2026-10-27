"""Trusted operator CLI; no public HTTP endpoint can publish or activate releases."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from psycopg_pool import AsyncConnectionPool

from backend.clients.catalog import PostgresClientCatalog
from backend.clients.releases import ReleaseSpec
from backend.clients.schema import apply_catalog_schema
from backend.config.guard import load_guard_config
from backend.config.images import load_image_config
from backend.config.models import load_model_config
from backend.config.services import load_service_config
from backend.config.settings import settings
from backend.config.workflows import load_workflow_spec
from backend.paths import CONFIG_DIR
from backend.tenancy import load_tenant
from backend.workflows.release_runtime import validate_release


def export_legacy(client_id: str) -> ReleaseSpec:
    """Freeze resolved file/environment settings; the runtime never rereads them."""
    tenant = load_tenant(client_id)
    services = load_service_config()
    if services.chat_graph not in {"recipe", "json_recipe"}:
        raise ValueError("Custom Python workflows require a reviewed JSON release definition")
    models = load_model_config(service_config=services)
    workflow = load_workflow_spec(CONFIG_DIR / "workflows" / "recipe_chat.json").model_dump(by_alias=True)

    def binding(implementation, *, role=None, prompts=()):
        return {"implementation": implementation,
                "model": getattr(models.assignments, role) if role else None,
                "prompts": {name: name for name in prompts}}

    by_kind = {
        "recipe_analysis": {"analyzer": binding(services.message_analyzer, role="message_analyzer", prompts=("analyze",))},
        "recipe_chat": {"chat": binding("llm", role="chat", prompts=("chat",))},
        "recipe_execute": {
            "search": binding(services.recipe_search, prompts=("recipe_not_found",)),
            "generation": binding(services.recipe_generation, role="recipe_generation",
                                  prompts=("generate_original", "generate_inspired")),
        },
        "recipe_contact": {"contact": binding(services.dietitian, prompts=("dietitian_contact",))},
        "recipe_response": {
            "writer": binding("llm", role="response", prompts=("general_response", "recipe_presentation")),
            "presentation": binding(services.recipe_presentation),
        },
    }
    for node in workflow["nodes"]:
        node["bindings"] = by_kind[node["kind"]]
    profile = tenant.profile.model_dump(mode="json", exclude={"dietitian_id"})
    spec = ReleaseSpec.model_validate({
        "client_id": client_id, "profile": profile, "workflow": workflow,
        "models": {name: model.model_dump() for name, model in models.models.items()},
        "prompts": {name: {"version": 1, "text": text} for name, text in tenant.prompts.items()},
        "guard": load_guard_config(tenant).model_dump(), "embedding_model": settings.EMBEDDING_MODEL,
        "image_recognition": load_image_config().model_dump(),
    })
    validate_release(spec)
    return spec


def read_release(path):
    spec = ReleaseSpec.model_validate_json(Path(path).read_text(encoding="utf-8-sig"))
    validate_release(spec)
    return spec


def write_release(path, spec):
    Path(path).write_text(json.dumps(spec.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


async def database_command(args):
    # Read/validate the release before making any external connection.
    spec = read_release(args.file) if args.command == "publish" else None
    async with AsyncConnectionPool(settings.db_conninfo(), min_size=0, max_size=2,
                                   kwargs={"autocommit": True, "prepare_threshold": 0}) as pool:
        catalog = PostgresClientCatalog(pool)
        if args.command == "migrate":
            await apply_catalog_schema(pool)
            print("Client catalog schema is ready.")
            return
        await catalog.check_schema()
        if args.command == "create-client":
            await catalog.create_client(args.client_id, args.slug, args.business_type)
            print(f"Created {args.client_id}: /c/{args.slug}")
        elif args.command == "publish":
            release = await catalog.publish(spec, activate=args.activate, adopt_legacy=args.adopt_legacy_checkpoints)
            print(f"Published {spec.client_id} v{release.version}: {release.release_id}")
        elif args.command == "activate":
            await catalog.activate(args.client_id, args.release_id)
            print("Active release changed. Existing conversation pins are unchanged.")
        elif args.command == "list":
            print(json.dumps(await catalog.list_releases(args.client_id), default=str, indent=2))
        elif args.command == "export":
            release = await catalog.get_release(args.client_id, args.release_id)
            write_release(args.output, release.spec)
            print(f"Exported version {release.version} to {args.output}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Create the additive client catalog tables")
    create = commands.add_parser("create-client")
    create.add_argument("--client-id", required=True)
    create.add_argument("--slug", required=True)
    create.add_argument("--business-type", required=True)
    legacy = commands.add_parser("export-legacy", help="Offline: freeze existing tenant configuration")
    legacy.add_argument("--client-id", required=True)
    legacy.add_argument("--output", required=True)
    validate = commands.add_parser("validate", help="Offline: check graph and binding contracts")
    validate.add_argument("file")
    publish = commands.add_parser("publish")
    publish.add_argument("file")
    publish.add_argument("--activate", action="store_true")
    publish.add_argument("--adopt-legacy-checkpoints", action="store_true")
    activate = commands.add_parser("activate", help="Activate a release, including rollback")
    activate.add_argument("--client-id", required=True)
    activate.add_argument("--release-id", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--client-id", required=True)
    export = commands.add_parser("export")
    export.add_argument("--client-id", required=True)
    export.add_argument("--release-id", required=True)
    export.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "export-legacy":
        write_release(args.output, export_legacy(args.client_id))
        print(f"Saved release document to {args.output}")
    elif args.command == "validate":
        spec = read_release(args.file)
        print(f"Valid release for {spec.client_id}: {spec.fingerprint()}")
    else:
        # Psycopg's async connection requires a selector loop on Windows.
        asyncio.run(database_command(args), loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
