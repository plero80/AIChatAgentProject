"""Real catalog/checkpoint transactions in the disposable loopback PostgreSQL fixture."""

import asyncio
import sys
from contextlib import asynccontextmanager

import httpx
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.errors import ForeignKeyViolation, RaiseException
from psycopg_pool import AsyncConnectionPool

from backend.clients.catalog import ClientNotFound, PostgresClientCatalog, ReleaseUnavailable
from backend.clients.schema import apply_catalog_schema
from backend.web.api import create_http_app
from backend.workflows.checkpoint_types import checkpoint_serializer


def run(coroutine):
    return asyncio.run(coroutine, loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)


@asynccontextmanager
async def store(database):
    async with AsyncConnectionPool(database.pool.conninfo, min_size=0, max_size=8,
                                   kwargs={"autocommit": True, "prepare_threshold": 0}) as pool:
        async with AsyncConnectionPool(database.pool.conninfo, min_size=0, max_size=4,
                                       kwargs={"autocommit": True}) as locks:
            await apply_catalog_schema(pool)
            await apply_catalog_schema(pool)  # Safe repeated migration.
            catalog = PostgresClientCatalog(pool, lock_pool=locks)
            await catalog.check_schema()
            yield catalog, pool


def test_publication_pin_rollback_restart_and_owner_constraints(isolated_database, release_document, release_factories):
    async def scenario():
        async with store(isolated_database()) as (catalog, pool):
            await catalog.create_client("chef_demo", "chef", "chef")
            await catalog.create_client("second", "second-chef", "chef")
            with pytest.raises(ReleaseUnavailable):
                await catalog.pin("chef", "before-publication")
            one = await catalog.publish(release_document(), factories=release_factories.factories, activate=True)
            assert (await catalog.pin("chef", "returning")).release_id == one.release_id
            two = await catalog.publish(release_document(version=2), factories=release_factories.factories, activate=True)
            assert two.version == 2
            restarted = PostgresClientCatalog(pool)
            assert (await restarted.pin("chef", "returning")).release_id == one.release_id
            assert (await restarted.pin("chef", "new")).release_id == two.release_id
            await catalog.activate("chef_demo", one.release_id)
            assert (await catalog.pin("chef", "after-rollback")).release_id == one.release_id
            assert (await catalog.pin("chef", "new")).release_id == two.release_id
            with pytest.raises(ReleaseUnavailable):
                await catalog.activate("second", one.release_id)
            with pytest.raises(ReleaseUnavailable):
                await catalog.get_release("second", one.release_id)
            with pytest.raises(ClientNotFound):
                await catalog.lookup("Demo Chef")  # Display names are never identities.
            async with pool.connection() as conn:
                with pytest.raises(ForeignKeyViolation):
                    await conn.execute("UPDATE clients SET active_release_id = %s WHERE id = 'second'", (one.release_id,))
                with pytest.raises(ForeignKeyViolation):
                    await conn.execute("INSERT INTO conversations VALUES ('second', 'x', %s, 'second:x', NOW())", (one.release_id,))
                with pytest.raises(RaiseException):
                    await conn.execute("UPDATE client_releases SET version = 9 WHERE id = %s", (one.release_id,))
                with pytest.raises(RaiseException):
                    await conn.execute("UPDATE conversations SET release_id = %s WHERE session_id = 'returning'", (two.release_id,))
                with pytest.raises(RaiseException):
                    await conn.execute("UPDATE clients SET slug = 'changed' WHERE id = 'chef_demo'")
                await conn.execute("UPDATE clients SET enabled = FALSE WHERE id = 'chef_demo'")
            with pytest.raises(ClientNotFound):
                await catalog.pin("chef", "returning")
    run(scenario())


def test_local_client_setup_preserves_active_releases_and_serves_both_profiles(isolated_database):
    from backend.clients.manage import export_legacy, read_release
    from backend.clients.setup_local import ensure_client
    from backend.paths import CONFIG_DIR

    async def scenario():
        async with store(isolated_database()) as (catalog, pool):
            # Publication of Alona's recipe workflow needs its ownership table.
            async with pool.connection() as connection:
                await connection.execute("CREATE TABLE dietitians (id TEXT PRIMARY KEY, display_name TEXT NOT NULL)")
            alona_spec = export_legacy("clinic_001")
            michael_spec = read_release(CONFIG_DIR / "releases" / "michael_lipkin.json")
            alona = await ensure_client(catalog, alona_spec, slug="alona", business_type="dietitian")
            michael = await ensure_client(catalog, michael_spec, slug="michael-lipkin", business_type="cooking_assistant")
            pin = await catalog.pin("michael-lipkin", "returning")
            repeated = await ensure_client(catalog, michael_spec, slug="michael-lipkin", business_type="cooking_assistant")
            assert repeated.release_id == michael.release_id == pin.release_id
            assert len(await catalog.list_releases("michael_lipkin")) == 1
            assert (await ensure_client(catalog, alona_spec, slug="alona", business_type="dietitian")).release_id == alona.release_id
            with pytest.raises(ValueError, match="different identity"):
                await ensure_client(catalog, michael_spec, slug="wrong-url", business_type="cooking_assistant")
            memory = AsyncPostgresSaver(pool, serde=checkpoint_serializer())
            await memory.setup()
            app = create_http_app(catalog=catalog, checkpointer=memory)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as browser:
                    first = (await browser.get("/api/clients/alona/public-config")).json()
                    second = (await browser.get("/api/clients/michael-lipkin/public-config")).json()
                    assert first["client_id"] == "clinic_001" and first["brand_name"] == "Alona"
                    assert second["client_id"] == "michael_lipkin" and second["brand_name"] == "Michael Lipkin"
                    assert first["release_id"] != second["release_id"]
                    assert second["image_upload"]["max_count"] == 10
    run(scenario())


def test_database_lifespan_owns_pools_and_ignores_legacy_files(
    isolated_database, release_document, release_factories, monkeypatch,
):
    async def scenario():
        database = isolated_database()
        async with store(database) as (catalog, pool):
            await catalog.create_client("chef_demo", "chef", "chef")
            await catalog.publish(release_document(), factories=release_factories.factories, activate=True)
            pools = []

            def pool_factory(**kwargs):
                value = AsyncConnectionPool(**kwargs)
                pools.append(value)
                return value

            pool_factory.check_connection = AsyncConnectionPool.check_connection
            monkeypatch.setattr("backend.web.client_routes.AsyncConnectionPool", pool_factory)
            monkeypatch.setattr("backend.bootstrap.get_db_conninfo", lambda: database.pool.conninfo)

            def forbidden(*args, **kwargs):
                raise AssertionError("Database mode must not load deployment files")

            monkeypatch.setattr("backend.web.api.load_tenant", forbidden)
            monkeypatch.setattr("backend.web.api.load_service_config", forbidden)
            app = create_http_app(config_source="database", release_factories=release_factories.factories)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as browser:
                    result = await browser.post("/api/clients/chef/chat", json={"message": "Hello"})
                    assert result.status_code == 200, result.text
                    assert result.json()["reply"] == "writer-one: turn 1"
            assert len(pools) == 3
            assert all(value.closed for value in pools)
            assert app.state.catalog is None
            assert release_factories.closed == ["writer-one"]
    run(scenario())


def test_prompt_versions_and_failed_publications_are_atomic(isolated_database, release_document, release_factories):
    async def scenario():
        async with store(isolated_database()) as (catalog, pool):
            await catalog.create_client("chef_demo", "chef", "chef")
            first = await catalog.publish(release_document(), factories=release_factories.factories, activate=True)
            changed = release_document().model_dump(mode="json", by_alias=True)
            changed["prompts"]["chef_support"]["text"] = "Changed without a version bump"
            from backend.clients.releases import ReleaseSpec
            with pytest.raises(ValueError, match="increment its version"):
                await catalog.publish(ReleaseSpec.model_validate(changed), factories=release_factories.factories, activate=True)
            assert len(await catalog.list_releases("chef_demo")) == 1
            assert (await catalog.lookup("chef")).active_release_id == first.release_id
            async with pool.connection() as conn:
                with pytest.raises(RaiseException):
                    await conn.execute("UPDATE client_prompt_versions SET template = 'mutated'")
    run(scenario())


def test_concurrent_first_requests_get_one_durable_pin(isolated_database, release_document, release_factories):
    async def scenario():
        async with store(isolated_database()) as (catalog, pool):
            await catalog.create_client("chef_demo", "chef", "chef")
            one = await catalog.publish(release_document(), factories=release_factories.factories, activate=True)
            two = await catalog.publish(release_document(version=2), factories=release_factories.factories)
            pins = await asyncio.gather(*[catalog.pin("chef", "racing") for _ in range(12)],
                                        catalog.activate("chef_demo", two.release_id))
            assert len({release.release_id for release in pins[:-1]}) == 1
            assert pins[0].release_id in {one.release_id, two.release_id}
            async with pool.connection() as conn:
                row = await (await conn.execute("SELECT COUNT(*) FROM conversations")).fetchone()
                assert row[0] == 1
    run(scenario())


def test_turn_lock_serializes_workers_and_releases_on_cancellation(isolated_database):
    async def scenario():
        async with store(isolated_database()) as (catalog, pool):
            other = PostgresClientCatalog(pool, lock_pool=catalog.lock_pool)
            held, entered, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()

            async def first():
                async with catalog.turn_lock("chef", "same-session"):
                    held.set()
                    await asyncio.Event().wait()

            async def second():
                waiting.set()
                async with other.turn_lock("chef", "same-session"):
                    entered.set()

            task = asyncio.create_task(first())
            await asyncio.wait_for(held.wait(), 5)
            follower = asyncio.create_task(second())
            await waiting.wait()
            # Other clients do not share this lock.
            async with other.turn_lock("another", "same-session"):
                assert not entered.is_set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(follower, 5)
            assert entered.is_set()
    run(scenario())


def test_legacy_checkpoint_adoption_preserves_exact_client_prefix(isolated_database, release_document, release_factories):
    async def scenario():
        async with store(isolated_database()) as (catalog, pool):
            await catalog.create_client("chef_demo", "chef", "chef")
            async with pool.connection() as conn:
                await conn.execute("CREATE TABLE checkpoints(thread_id TEXT)")
                for thread in ("chef_demo:old", "chef_demo:old", "chefXdemo:other", "second:old"):
                    await conn.execute("INSERT INTO checkpoints VALUES (%s)", (thread,))
            one = await catalog.publish(release_document(), factories=release_factories.factories,
                                        activate=True, adopt_legacy=True)
            two = await catalog.publish(release_document(version=2), factories=release_factories.factories, activate=True)
            assert (await catalog.pin("chef", "old")).release_id == one.release_id
            assert (await catalog.pin("chef", "new")).release_id == two.release_id
            with pytest.raises(ValueError, match="initial release"):
                await catalog.publish(release_document(version=3), factories=release_factories.factories, adopt_legacy=True)
            async with pool.connection() as conn:
                rows = await (await conn.execute("SELECT thread_id FROM conversations ORDER BY thread_id")).fetchall()
                assert rows == [("chef_demo:new",), ("chef_demo:old",)]
    run(scenario())


def test_http_routes_release_selection_history_and_restart_with_postgres(
    isolated_database, release_document, release_factories, offline_admission, monkeypatch, tmp_path,
):
    async def scenario():
        async with store(isolated_database()) as (catalog, pool):
            await catalog.create_client("chef_demo", "chef", "chef")
            await catalog.create_client("second", "second", "other_business")
            old = await catalog.publish(release_document(), factories=release_factories.factories, activate=True)
            await catalog.publish(release_document("second", model="second-model"), factories=release_factories.factories, activate=True)
            memory = AsyncPostgresSaver(pool, serde=checkpoint_serializer())
            await memory.setup()
            images = tmp_path / "images"
            (images / "chef_demo").mkdir(parents=True)
            (images / "second").mkdir()
            (images / "chef_demo" / "only-first.png").write_bytes(b"fake image")
            monkeypatch.setattr("backend.web.client_routes.image_directory", lambda identity: images / identity)

            def application():
                return create_http_app(catalog=catalog, checkpointer=memory, release_factories=release_factories.factories)

            app = application()
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as browser:
                    cfg = await browser.get("/api/clients/chef/public-config")
                    assert cfg.status_code == 200
                    assert cfg.json()["release_id"] == old.release_id
                    assert not {"models", "prompts", "workflow", "guard"} & cfg.json().keys()
                    assert browser.cookies.get("alona_session")
                    assert (await browser.post("/api/clients/chef/chat", json={"message": "Dinner"})).json()["reply"] == "writer-one: turn 1"
                    new = await catalog.publish(release_document(version=2, model="writer-two"),
                                                factories=release_factories.factories, activate=True)
                    assert (await browser.get("/api/clients/chef/public-config")).json()["release_id"] == old.release_id
                    assert (await browser.post("/api/clients/chef/chat", json={"message": "Another"})).json()["reply"] == "writer-one: turn 2"
                    assert (await browser.post("/api/clients/second/chat", json={"message": "Hi"})).json()["reply"] == "second-model: turn 1"
                    assert (await browser.post("/api/clients/missing/chat", json={"message": "Hi"})).status_code == 404
                    assert (await browser.post("/api/clients/chef/chat", json={"message": "Hi", "release_id": new.release_id})).status_code == 422
                    assert (await browser.get("/api/clients/chef/recipe-images/only-first.png")).status_code == 200
                    assert (await browser.get("/api/clients/second/recipe-images/only-first.png")).status_code == 404
                    cookies = httpx.Cookies(browser.cookies)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as newcomer:
                    assert (await newcomer.post("/api/clients/chef/chat", json={"message": "First"})).json()["reply"] == "writer-two: turn 1"
            # Rebuild facade/providers; PostgreSQL history and pin stay the same.
            restarted = application()
            async with restarted.router.lifespan_context(restarted):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(restarted), base_url="http://test", cookies=cookies) as browser:
                    assert (await browser.post("/api/clients/chef/chat", json={"message": "Still here"})).json()["reply"] == "writer-one: turn 3"
                    session_id = browser.cookies.get("alona_session")
                    before = await memory.aget_tuple({"configurable": {"thread_id": f"chef_demo:{session_id}"}})
                    offline_admission.handler = lambda req: httpx.Response(200, json={"answers": [
                        {"type": "predicate", "name": "unsafe_request", "probability": 1.0},
                        {"type": "predicate", "name": "in_support_scope", "probability": 1.0},
                    ]})
                    answer = await browser.post("/api/clients/chef/chat", json={"message": "Rejected"})
                    assert answer.json()["reply"] == old.spec.guard.policy.unsafe_reply
                    after = await memory.aget_tuple({"configurable": {"thread_id": f"chef_demo:{session_id}"}})
                    assert before.checkpoint["id"] == after.checkpoint["id"]
            assert len(release_factories.closed) == len(release_factories.models)
    run(scenario())


def test_photo_enabled_release_and_existing_text_only_pin(
    isolated_database, release_document, release_factories, photo_payload, photo_payloads, offline_admission, monkeypatch,
):
    from backend.clients.releases import ReleaseSpec
    from backend.services.chat.image_recognizer import BaseImageRecognizer, ImageObservation

    calls = []

    class Recognizer(BaseImageRecognizer):
        async def recognize(self, images, message):
            calls.append((images, message))
            return ImageObservation(ingredients=["tomato"], uncertain_items=["herbs"], description="Tomato on a board")

    release_factories.factories.image_recognizer.register("test_vision", lambda **deps: Recognizer())
    monkeypatch.setattr("backend.web.api.enforce_rate_limits", lambda *_: None)

    async def scenario():
        async with store(isolated_database()) as (catalog, pool):
            await catalog.create_client("chef_demo", "chef", "chef")
            old_data = release_document().model_dump(mode="json", by_alias=True)
            old_data.pop("image_recognition", None)
            old = await catalog.publish(ReleaseSpec.model_validate(old_data), factories=release_factories.factories, activate=True)
            memory = AsyncPostgresSaver(pool, serde=checkpoint_serializer())
            await memory.setup()
            app = create_http_app(catalog=catalog, checkpointer=memory, release_factories=release_factories.factories)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as existing:
                    assert not (await existing.get("/api/clients/chef/public-config")).json()["image_upload"]["enabled"]
                    new_data = release_document(version=2).model_dump(mode="json", by_alias=True)
                    new_data["image_recognition"] = {
                        "implementation": "test_vision", "model": {"provider": "local", "model": "vision-test", "max_tokens": 100},
                        "prompt": "Identify ingredients", "timeout_seconds": 1.0,
                    }
                    await catalog.publish(ReleaseSpec.model_validate(new_data), factories=release_factories.factories, activate=True)
                    assert (await existing.get("/api/clients/chef/public-config")).json()["release_id"] == old.release_id
                    denied = await existing.post("/api/clients/chef/chat", json={"image": photo_payload})
                    assert denied.status_code == 422
                    assert not calls
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as browser:
                    capability = (await browser.get("/api/clients/chef/public-config")).json()["image_upload"]
                    assert capability["enabled"] and capability["max_count"] == 10
                    too_many = await browser.post("/api/clients/chef/chat", json={"images": photo_payloads + [photo_payload]})
                    assert too_many.status_code == 422
                    assert not calls and not offline_admission.calls
                    reply = await browser.post("/api/clients/chef/chat", json={"message": "Something salty", "images": photo_payloads})
                    assert reply.status_code == 200, reply.text
                    assert len(calls) == 1 and calls[0][1] == "Something salty"
                    images = calls[0][0]
                    assert len(images) == 10 and all(image.mime_type == "image/jpeg" for image in images)
                    assert len({image.data for image in images}) == 10
                    content = offline_admission.calls[-1]["input"][0]["content"]
                    assert [part["image_url"] for part in content[1:]] == [image.data_url for image in images]
                    thread = "chef_demo:" + browser.cookies.get("alona_session")
                    saved = await memory.aget_tuple({"configurable": {"thread_id": thread}})
                    history = saved.checkpoint["channel_values"]["messages"]
                    assert "Something salty" in history[0].content and "tomato" in history[0].content
                    assert "herbs" in history[0].content and "uncertain_items" in history[0].content
                    assert "data:image" not in str(saved.checkpoint)
                    assert all(photo["data"] not in str(saved.checkpoint) for photo in photo_payloads)
                    assert all(image.data not in str(saved.checkpoint) for image in images)
                    assert (await browser.post("/api/clients/chef/chat", json={"message": "Another idea"})).status_code == 200
                    assert len(calls) == 1
            assert "vision-test" in release_factories.closed
    run(scenario())
