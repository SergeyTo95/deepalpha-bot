import asyncio

import pytest

import velia_mobile_attachment_routes as routes


def test_cancelled_upload_is_scrubbed_after_worker_completes(monkeypatch):
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        deleted = []

        async def fake_to_thread(function, *args, **kwargs):
            if function is routes.create_attachment_with_reservation:
                started.set()
                await release.wait()
                return {"id": "attachment-1"}
            if function is routes.delete_attachment:
                deleted.append((args, kwargs))
                return True
            raise AssertionError(f"unexpected function: {function}")

        monkeypatch.setattr(routes.asyncio, "to_thread", fake_to_thread)

        request_task = asyncio.create_task(
            routes._create_attachment_recoverably(
                user_id=7,
                conversation_id="conversation-1",
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

        assert deleted == [((7, "attachment-1"), {})]

    asyncio.run(scenario())


def test_successful_upload_is_not_deleted(monkeypatch):
    async def scenario():
        deleted = []

        async def fake_to_thread(function, *args, **kwargs):
            if function is routes.create_attachment_with_reservation:
                return {"id": "attachment-2"}
            if function is routes.delete_attachment:
                deleted.append((args, kwargs))
                return True
            raise AssertionError(f"unexpected function: {function}")

        monkeypatch.setattr(routes.asyncio, "to_thread", fake_to_thread)

        result = await routes._create_attachment_recoverably(
            user_id=7,
            conversation_id="conversation-1",
            filename="report.txt",
            mime_type="text/plain",
            content=b"hello",
        )

        assert result["id"] == "attachment-2"
        assert deleted == []

    asyncio.run(scenario())
