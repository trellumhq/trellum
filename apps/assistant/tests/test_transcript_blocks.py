"""Replayed content blocks carry only what the Messages API accepts."""
from anthropic.types import TextBlock, ToolUseBlock

from apps.assistant.llm import _block_to_dict


def test_text_block_drops_sdk_extras():
    block = TextBlock(type="text", text="hi", citations=None)
    object.__setattr__(block, "parsed_output", {"x": 1})  # what the streaming snapshot adds
    assert _block_to_dict(block) == {"type": "text", "text": "hi"}


def test_tool_use_block_drops_caller_and_keeps_input():
    block = ToolUseBlock(type="tool_use", id="toolu_1", name="list_reports", input={"category": "Revenue"})
    assert _block_to_dict(block) == {
        "type": "tool_use", "id": "toolu_1", "name": "list_reports", "input": {"category": "Revenue"},
    }


def test_plain_dict_blocks_pass_through_minus_nones():
    assert _block_to_dict({"type": "text", "text": "x", "citations": None}) == {"type": "text", "text": "x"}
