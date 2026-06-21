import asyncio
from pathlib import Path
from typing import List


def _load_splitter():
    src = Path("telegram_bot.py").read_text()
    start = src.index("def split_telegram_text")
    end = src.index("async def _send_live_final_chunks", start)
    ns = {"List": List}
    exec(src[start:end], ns)
    return ns["split_telegram_text"]


def test_split_telegram_text_does_not_cut_short_answer():
    split_telegram_text = _load_splitter()
    assert split_telegram_text("Short complete answer.") == ["Short complete answer."]


def test_split_telegram_text_splits_long_answer_into_safe_chunks():
    split_telegram_text = _load_splitter()
    text = ("Sentence one is complete. " * 300)
    chunks = split_telegram_text(text, max_len=3500)
    assert len(chunks) > 1
    assert all(len(chunk) <= 3500 for chunk in chunks)
    assert all(chunk.endswith(".") for chunk in chunks[:-1])


def _load_live_sender():
    src = Path("telegram_bot.py").read_text()
    start = src.index("def split_telegram_text")
    end = src.index("def _escape", start)
    ns = {"List": List, "LIVE_UNAVAILABLE_MESSAGE": "unavailable"}
    exec(src[start:end], ns)
    return ns["_send_live_final_chunks"]


class FakeThinking:
    def __init__(self, fail_edit=False):
        self.fail_edit = fail_edit
        self.edits = []
        self.deleted = False

    async def edit_text(self, text, reply_markup=None):
        if self.fail_edit:
            raise RuntimeError("edit failed")
        self.edits.append((text, reply_markup))

    async def delete(self):
        self.deleted = True


class FakeMessage:
    def __init__(self):
        self.answers = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))


def test_live_sender_edits_first_chunk_and_sends_remaining():
    async def run():
        sender = _load_live_sender()
        message = FakeMessage()
        thinking = FakeThinking()
        await sender(message, thinking, "First sentence. " * 260, final_markup="kb", max_len=3500)
        assert len(thinking.edits) == 1
        assert thinking.edits[0][1] is None
        assert len(message.answers) >= 1
        assert message.answers[-1][1] == "kb"
    asyncio.run(run())


def test_live_sender_deletes_thinking_before_fallback_when_edit_fails():
    async def run():
        sender = _load_live_sender()
        message = FakeMessage()
        thinking = FakeThinking(fail_edit=True)
        await sender(message, thinking, "Final answer", final_markup="kb", max_len=3500)
        assert thinking.deleted is True
        assert message.answers == [("Final answer", "kb")]
    asyncio.run(run())
