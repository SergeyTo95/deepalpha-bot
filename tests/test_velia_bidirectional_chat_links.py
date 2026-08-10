from pathlib import Path

from services import velia_conversation_links_bidirectional_service as service


def test_peer_map_is_symmetric_for_one_stored_edge():
    peers = service._peer_map([("chat-a", "chat-b")])

    assert peers == {
        "chat-a": {"chat-b"},
        "chat-b": {"chat-a"},
    }


def test_numeric_memory_fact_survives_linked_context_rendering():
    number = "644567889975321"
    selected = [
        {
            "source_id": "memory-chat",
            "source_title": "Запомни число",
            "role": "user",
            "content": f"запомни число: {number}",
            "message_id": "m1",
        },
        {
            "source_id": "memory-chat",
            "source_title": "Запомни число",
            "role": "assistant",
            "content": f"Приняла: число {number}.",
            "message_id": "m2",
        },
    ]

    context = service._render_linked_context(selected)

    assert number in context
    assert "SOURCE CHAT: Запомни число" in context
    assert "USER: запомни число" in context


def test_runtime_patches_routes_and_prompt_to_bidirectional_contract():
    runtime = Path("services/velia_telegram_connect_page_patch.py").read_text(encoding="utf-8")

    assert "links_routes_module.link_conversations = link_conversations_bidirectional" in runtime
    assert "links_routes_module.list_conversation_links = list_conversation_links_bidirectional" in runtime
    assert "links_routes_module.unlink_conversation = unlink_conversation_bidirectional" in runtime
    assert "list_conversation_link_summaries_bidirectional" in runtime
    assert "links_service_module.build_linked_context = build_linked_context_bidirectional" in runtime
    assert runtime.index("links_service_module.build_linked_context = build_linked_context_bidirectional") < runtime.index(
        "links_service_module.install_linked_conversation_prompt(chat_service_module)"
    )


def test_bidirectional_service_reads_and_unlinks_either_stored_direction():
    source = Path("services/velia_conversation_links_bidirectional_service.py").read_text(encoding="utf-8")

    assert "l.target_conversation_id=%s OR l.source_conversation_id=%s" in source
    assert "(target_conversation_id=%s AND source_conversation_id=%s)" in source
    assert source.count("(target_conversation_id=%s AND source_conversation_id=%s)") >= 2


def test_stale_edges_are_deleted_before_peer_capacity_is_counted():
    class RecordingCursor:
        def __init__(self):
            self.calls = []

        def execute(self, query, params=None):
            self.calls.append((" ".join(str(query).split()), tuple(params or ())))

    cursor = RecordingCursor()
    service._delete_inactive_edges_touching(cursor, 17, ["chat-a", "chat-b"])

    assert len(cursor.calls) == 1
    query, params = cursor.calls[0]
    assert query.startswith("DELETE FROM velia_conversation_links AS l")
    assert query.count("NOT EXISTS") == 2
    assert "l.target_conversation_id IN (%s,%s)" in query
    assert "l.source_conversation_id IN (%s,%s)" in query
    assert params == (17, "chat-a", "chat-b", "chat-a", "chat-b")

    source = Path("services/velia_conversation_links_bidirectional_service.py").read_text(encoding="utf-8")
    cleanup_call = "_delete_inactive_edges_touching(cursor, uid, participant_ids)"
    active_edge_call = "edges = _active_edges(cursor, uid)"
    assert cleanup_call in source
    assert source.index(cleanup_call) < source.index(active_edge_call)


def test_badges_are_emitted_for_both_edge_participants():
    peers = service._peer_map([
        ("chat-a", "chat-b"),
        ("chat-a", "chat-c"),
    ])

    summaries = {
        chat_id: len(linked) + 1
        for chat_id, linked in peers.items()
    }

    assert summaries == {
        "chat-a": 3,
        "chat-b": 2,
        "chat-c": 2,
    }
