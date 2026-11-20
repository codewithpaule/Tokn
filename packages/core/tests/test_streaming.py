from __future__ import annotations

import json

import pytest

from tokn_core.streaming import SseSplitter, UsageAccumulator, sse_payload


def event(payload: dict) -> bytes:
    return b"data: " + json.dumps(payload).encode() + b"\n\n"


def test_only_data_lines_carry_a_payload() -> None:
    assert sse_payload(b"data: {}\n") == b"{}"
    assert sse_payload(b"event: message_start") is None
    assert sse_payload(b": keep-alive") is None
    assert sse_payload(b"data: [DONE]") is None
    assert sse_payload(b"data:") is None


def test_openai_usage_comes_from_the_final_chunk() -> None:
    usage = UsageAccumulator()
    usage.feed("openai", event({"choices": [{"delta": {"content": "hi"}}]}))
    assert not usage.saw_usage

    usage.feed(
        "openai",
        event(
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 1200,
                    "completion_tokens": 340,
                    "prompt_tokens_details": {"cached_tokens": 800},
                },
            }
        ),
    )
    assert usage.saw_usage
    assert usage.usage().input_tokens == 1200
    assert usage.usage().output_tokens == 340
    assert usage.usage().cache_read_tokens == 800


def test_anthropic_usage_is_split_across_two_events() -> None:
    usage = UsageAccumulator()
    usage.feed(
        "anthropic",
        event(
            {
                "type": "message_start",
                "message": {
                    "usage": {
                        "input_tokens": 900,
                        "cache_read_input_tokens": 100,
                        "cache_creation_input_tokens": 50,
                    }
                },
            }
        ),
    )
    usage.feed("anthropic", event({"type": "content_block_delta", "delta": {"text": "x"}}))
    assert usage.usage().output_tokens == 0

    usage.feed("anthropic", event({"type": "message_delta", "usage": {"output_tokens": 210}}))

    tokens = usage.usage()
    assert tokens.input_tokens == 900
    assert tokens.output_tokens == 210
    assert tokens.cache_read_tokens == 100
    assert tokens.cache_write_tokens == 50


def test_a_malformed_event_never_raises() -> None:
    usage = UsageAccumulator()
    usage.feed("openai", b"data: {not json}\n")
    usage.feed("anthropic", b"data: [1, 2, 3]\n")
    usage.feed("openai", b"data: null\n")
    assert not usage.saw_usage
    assert usage.chunks_seen == 0


def test_anthropic_events_without_usage_are_ignored() -> None:
    usage = UsageAccumulator()
    usage.feed("anthropic", event({"type": "message_start"}))
    usage.feed("anthropic", event({"type": "message_start", "message": "not-a-dict"}))
    usage.feed("anthropic", event({"type": "message_delta"}))
    usage.feed("anthropic", event({"type": "message_delta", "usage": []}))

    assert not usage.saw_usage
    assert usage.usage().input_tokens == 0
    assert usage.usage().output_tokens == 0


def test_negative_or_non_integer_counts_are_treated_as_zero() -> None:
    usage = UsageAccumulator()
    usage.feed(
        "openai",
        event({"usage": {"prompt_tokens": "many", "completion_tokens": True}}),
    )
    assert usage.usage().input_tokens == 0
    assert usage.usage().output_tokens == 0


def test_an_empty_stream_reports_no_usage() -> None:
    usage = UsageAccumulator()
    assert usage.saw_usage is False
    assert usage.usage().input_tokens == 0


@pytest.mark.parametrize("line", [b"", b"\n", b"   ", b"data:   \n"])
def test_blank_lines_are_ignored(line: bytes) -> None:
    usage = UsageAccumulator()
    usage.feed("openai", line)
    assert usage.chunks_seen == 0


def test_a_network_chunk_can_hold_part_of_a_line() -> None:
    splitter = SseSplitter()
    assert splitter.feed(b'data: {"a":') == []
    assert splitter.feed(b'1}\n\ndata: {"b":2}\n\n') == [
        b'data: {"a":1}',
        b"",
        b'data: {"b":2}',
        b"",
    ]
    assert splitter.flush() == []


def test_a_trailing_partial_line_is_returned_by_flush() -> None:
    splitter = SseSplitter()
    assert splitter.feed(b"data: {}\ndata: {") == [b"data: {}"]
    assert splitter.flush() == [b"data: {"]


def test_usage_survives_arbitrary_chunk_boundaries() -> None:
    payload = event({"usage": {"prompt_tokens": 10, "completion_tokens": 20}})
    for cut in range(1, len(payload)):
        usage = UsageAccumulator()
        splitter = SseSplitter()
        for line in splitter.feed(payload[:cut]) + splitter.feed(payload[cut:]):
            usage.feed("openai", line)
        for line in splitter.flush():
            usage.feed("openai", line)
        assert usage.usage().output_tokens == 20, f"lost usage when cut at {cut}"
