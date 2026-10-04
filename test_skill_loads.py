import json
import time
import pytest
from test_otel_plugin import make_runtime, metric_points


def event(**updates):
    data = dict(tool_name="skill_view", status="ok", tool_call_id="call", session_id="session",
                args={"name": "alias", "secret": "private input"},
                result=json.dumps({"success": True, "name": "hermes-agent", "content": "private body",
                                   "description": "private description", "_source_path": "/private/path"}))
    data.update(updates)
    return data


@pytest.mark.parametrize("capture_content", [False, True])
def test_success_duplicates_repeats_privacy_and_flush(capture_content):
    runtime, exporter, reader = make_runtime(capture_content=capture_content)
    data = event()
    before = time.time_ns()
    runtime.pre_tool(**data)
    runtime.post_tool(**data)
    runtime.pre_tool(**data)  # repeated pre/post callback must not count again
    runtime.post_tool(**data)
    runtime.post_tool(**event(tool_call_id="repeat"))
    runtime.post_tool(**event(session_id="other"))
    after = time.time_ns()
    assert runtime.force_flush()
    spans = [s for s in exporter.get_finished_spans() if s.name == "skill.load"]
    expected = {"runtime": "hermes", "skill_name": "hermes-agent", "load_kind": "explicit", "status": "ok"}
    assert len(spans) == 3
    for span in spans:
        assert dict(span.attributes) == expected
        assert not span.events
        assert before <= span.start_time <= after
        assert span.end_time == span.start_time
    points = metric_points(reader)
    assert points["hermes.skill.loads"][0].value == 3
    assert dict(points["hermes.skill.loads"][0].attributes) == expected
    assert points["hermes.tool.calls"][0].value == 2  # generic semantics unchanged
    assert "private" not in repr(spans)
    runtime.shutdown()


@pytest.mark.parametrize("updates", [
    {"status": "error"}, {"tool_name": "skills_list"}, {"tool_name": "skill_manage"},
    {"args": {"file_path": "references/api.md"}},
    {"args": {"file_path": "SKILL.md"}}, {"args": None},
    {"result": {"success": False, "name": "x", "content": "x"}},
    {"result": {"success": True, "name": "x", "content": "x", "error": "failure"}},
    {"result": {"success": True, "name": "x"}}, {"result": "invalid json"},
    {"result": []}, {"tool_call_id": None},
    {"result": {"success": True, "name": "/private/path", "content": "x"}},
    {"result": {"success": True, "name": "x\nsecret", "content": "x"}},
])
def test_non_loads_fail_closed(updates):
    runtime, exporter, reader = make_runtime()
    runtime.post_tool(**event(**updates))
    assert runtime.force_flush()
    assert not exporter.get_finished_spans()
    assert "hermes.skill.loads" not in metric_points(reader)
    runtime.shutdown()


def test_qualified_names_no_alias_merging_and_bounded_replay_window():
    runtime, exporter, reader = make_runtime()
    runtime._skill_load_seen_limit = 2
    for call, name in [("1", "plugin:Skill_Name"), ("2", "Skill_Name"), ("3", "plugin:skill_name")]:
        runtime.post_tool(**event(tool_call_id=call, result={"success": True, "name": name, "content": "secret"}))
    assert len(runtime._skill_load_seen) == 2
    assert list(runtime._skill_load_seen) == [("session", "2"), ("session", "3")]
    assert runtime.force_flush()
    assert {p.attributes["skill_name"] for p in metric_points(reader)["hermes.skill.loads"]} == {
        "plugin:Skill_Name", "Skill_Name", "plugin:skill_name"}
    assert len(exporter.get_finished_spans()) == 3
    runtime.shutdown()
