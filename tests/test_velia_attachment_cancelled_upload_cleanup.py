import asyncio

import pytest

import velia_mobile_attachment_routes as routes


def test_cancelled_upload_remains_recoverable_after_worker_completes(monkeypatch):
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        completed = []

        async def fake_to_thread(function, *args, **kwargs):
            assert function is routes.create_attachment_idempotently
            assert kwargs["idempotency_key"] == "draft-12345678"
            started.set()
            await release.wait()
            completed.append((args, kwargs))
            return {"id": "attachment-1"}

        monkeypatch.setattr(routes.asyncio, "to_thread", fake_to_thread)

        request_task = asyncio.create_task(
            routes._create_attachment_recoverably(
                user_id=7,
                conversation_id="conversation-1",
                idempotency_key="draft-12345678",
                filename="report.txt",
                mime_type="text/plain",
                content=b"hello",
            )
        )
        await started.wait()
        request_task.cancel()
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await request_task
        await asyncio.sleep(0)

        assert len(completed) == 1

    asyncio.run(scenario())


def test_successful_upload_returns_idempotent_result(monkeypatch):
    async def scenario():
        calls = []

        async def fake_to_thread(function, *args, **kwargs):
            assert function is routes.create_attachment_idempotently
            calls.append((args, kwargs))
            return {"id": "attachment-2"}

        monkeypatch.setattr(routes.asyncio, "to_thread", fake_to_thread)

        result = await routes._create_attachment_recoverably(
            user_id=7,
            conversation_id="conversation-1",
            idempotency_key="draft-12345678",
            filename="report.txt",
            mime_type="text/plain",
            content=b"hello",
        )

        assert result["id"] == "attachment-2"
        assert calls[0][1]["idempotency_key"] == "draft-12345678"

    asyncio.run(scenario())
