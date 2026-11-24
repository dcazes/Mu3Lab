"""A change runs only as it was read back, after the person heard it in full and answered in a
later turn; the model judges what they meant, the code owns when it may run (plan item 4.1)."""

import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import agent
import pytest
from livekit.agents import ToolError, mcp


class FakeTool:
    """An app's MCP tool: records what it was asked to do."""

    def __init__(self, name: str) -> None:
        self.info = SimpleNamespace(
            name=name,
            raw_schema={"name": name, "description": f"{name}.", "parameters": {"type": "object", "properties": {}}},
        )
        self.calls: list[dict] = []

    async def __call__(self, *, raw_arguments: dict) -> dict:
        self.calls.append(raw_arguments)
        return {"done": True}


@asynccontextmanager
async def no_filler(*_args, **_kwargs):
    yield


@pytest.fixture
def setup():
    state = agent.CallState(settings=agent.load_settings())
    toolset = agent.ConfirmedToolset(
        id="calendar", mcp_server=mcp.MCPServerHTTP(url="http://unused"), reads=["list_events"]
    )
    read, write = FakeTool("cal-list_events"), FakeTool("cal-create_event")
    tools = {tool.info.name: tool for tool in toolset.arrange([read, write])}
    context = SimpleNamespace(userdata=state, with_filler=no_filler)
    return state, tools, context, read, write


def said(state: agent.CallState, role: str, *, interrupted: bool = False, at: float | None = None) -> None:
    state.heard(role, interrupted, time.time() if at is None else at)


async def test_reads_run_at_once_and_writes_wait_for_a_heard_yes(setup) -> None:
    state, tools, context, read, write = setup
    assert set(tools) == {"cal-list_events", "cal-create_event", "confirm_change"}
    assert "only prepares the change" in tools["cal-create_event"].info.raw_schema["description"]

    await tools["cal-list_events"]._func({"day": "today"}, context)
    assert read.calls == [{"day": "today"}]

    said(state, "user")  # "Add dinner at seven"
    prepared = await tools["cal-create_event"]._func({"title": "Dinner", "start": "19:00"}, context)
    assert prepared["status"] == "needs_confirmation" and not write.calls
    said(state, "assistant")  # the read-back, played in full
    said(state, "user")  # "Yes please"
    await tools["confirm_change"]._func(context, prepared["action_id"])
    assert write.calls == [{"title": "Dinner", "start": "19:00"}]
    # One confirm makes one change.
    with pytest.raises(ToolError, match="latest prepared change"):
        await tools["confirm_change"]._func(context, prepared["action_id"])
    assert len(write.calls) == 1


async def test_no_answer_yet_is_refused(setup) -> None:
    state, tools, context, _read, write = setup
    prepared = await tools["cal-create_event"]._func({"title": "Dinner"}, context)
    said(state, "assistant")
    with pytest.raises(ToolError, match="not answered yet"):
        await tools["confirm_change"]._func(context, prepared["action_id"])
    assert not write.calls


async def test_an_interrupted_read_back_is_refused(setup) -> None:
    state, tools, context, _read, write = setup
    prepared = await tools["cal-create_event"]._func({"title": "Dinner"}, context)
    said(state, "assistant", interrupted=True)
    said(state, "user")
    with pytest.raises(ToolError, match="not heard the details in full"):
        await tools["confirm_change"]._func(context, prepared["action_id"])
    assert not write.calls


async def test_words_said_before_the_tool_ran_are_not_the_read_back(setup) -> None:
    state, tools, context, _read, write = setup
    before = time.time() - 5  # "Sure, I'll add that" began before the change was prepared
    prepared = await tools["cal-create_event"]._func({"title": "Dinner"}, context)
    said(state, "assistant", at=before)
    said(state, "user")
    with pytest.raises(ToolError, match="not heard"):
        await tools["confirm_change"]._func(context, prepared["action_id"])
    assert not write.calls


async def test_a_newer_proposal_replaces_the_old_one(setup) -> None:
    state, tools, context, _read, write = setup
    first = await tools["cal-create_event"]._func({"title": "Dinner", "start": "19:00"}, context)
    said(state, "assistant")
    said(state, "user")  # "Make it eight"
    second = await tools["cal-create_event"]._func({"title": "Dinner", "start": "20:00"}, context)
    with pytest.raises(ToolError, match="latest prepared change"):
        await tools["confirm_change"]._func(context, first["action_id"])
    said(state, "assistant")
    said(state, "user")
    await tools["confirm_change"]._func(context, second["action_id"])
    assert write.calls == [{"title": "Dinner", "start": "20:00"}]


async def test_another_apps_assistant_cannot_confirm_it(setup) -> None:
    state, tools, context, _read, write = setup
    prepared = await tools["cal-create_event"]._func({"title": "Dinner"}, context)
    said(state, "assistant")
    said(state, "user")
    with pytest.raises(ToolError, match="latest prepared change"):
        state.take("photos", prepared["action_id"])
    assert state.pending is not None and not write.calls


def test_an_app_with_only_reads_has_no_confirm_tool() -> None:
    toolset = agent.ConfirmedToolset(id="kopia", mcp_server=mcp.MCPServerHTTP(url="http://unused"), reads=["list"])
    assert [tool.info.name for tool in toolset.arrange([FakeTool("k-list")])] == ["k-list"]
