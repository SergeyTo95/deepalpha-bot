from pathlib import Path

import pytest

from services.velia_conversation_ux_order_service import merge_partial_conversation_order


def test_partial_reorder_preserves_hidden_conversations():
    current = [f"chat-{index}" for index in range(60)]
    submitted = list(current[:50])
    submitted.insert(0, submitted.pop(12))

    result = merge_partial_conversation_order(current, submitted)

    assert result[:50] == submitted
    assert result[50:] == current[50:]


def test_partial_reorder_can_move_visible_tail_to_front():
    current = ["a", "b", "c", "d", "e"]

    result = merge_partial_conversation_order(current, ["c", "a", "b"])

    assert result == ["c", "a", "b", "d", "e"]


def test_partial_reorder_rejects_non_subset():
    with pytest.raises(ValueError, match="submitted_order_not_subset"):
        merge_partial_conversation_order(["a", "b"], ["a", "missing"])


def test_runtime_installs_pagination_safe_reorder_before_routes():
    source = Path("services/velia_telegram_connect_page_patch.py").read_text(encoding="utf-8")
    assignment = "conversation_ux_routes_module.reorder_conversations = reorder_visible_conversations"
    assert assignment in source
    assert source.index(assignment) < source.index("setup_velia_conversation_ux_routes(app, mobile_routes_module)")


def test_runtime_uses_same_canonical_listing_as_reorder():
    runtime = Path("services/velia_telegram_connect_page_patch.py").read_text(encoding="utf-8")
    order_service = Path("services/velia_conversation_ux_order_service.py").read_text(encoding="utf-8")

    assert "chat_service_module.list_conversations = list_conversations_ordered_stable" in runtime
    assert "mobile_routes_module.list_conversations = list_conversations_ordered_stable" in runtime
    assert "c.updated_at DESC,\n              c.conversation_id ASC" in order_service
    assert order_service.count("c.conversation_id ASC") >= 2


def test_link_summary_route_is_bulk_and_owner_authenticated():
    source = Path("services/velia_conversation_links_routes.py").read_text(encoding="utf-8")
    assert '"/mobile-api/v1/conversation-links/summary"' in source
    assert "list_conversation_link_summaries(int(auth[\"user_id\"]))" in source


def test_summary_counts_only_active_linked_sources():
    source = Path("services/velia_conversation_links_summary_service.py").read_text(encoding="utf-8")
    assert "source.deleted_at IS NULL AND source.is_archived=FALSE" in source
    assert "target.deleted_at IS NULL AND target.is_archived=FALSE" in source
    assert '"linked_group_size": source_count + 1' in source
