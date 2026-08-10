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
