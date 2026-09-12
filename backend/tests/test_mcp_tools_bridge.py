"""Contract tests between the research stage and the direct MCP tools."""

import asyncio
import json


def test_mcp_config_resolves_from_project_root(monkeypatch):
    from app import mcp_tools

    monkeypatch.setattr(mcp_tools, "_load_dotenv", lambda _path: None)
    assert mcp_tools.CONFIG_PATH == mcp_tools.PROJECT_ROOT / "mcp_config.yaml"
    assert mcp_tools.CONFIG_PATH.is_file()
    expected = {
        "output",
        "retry",
        "http",
        "apify",
        "realping",
        "google_maps",
        "openai",
    }
    assert expected <= set(mcp_tools.load_config())


def test_research_provider_aliases_target_direct_tools():
    from app import mcp

    servers = {
        name: factory() for name, factory in mcp._provider_factories().items()
    }

    assert {name: server.tools for name, server in servers.items()} == {
        "maps": ("googleMaps",),
        "apify_threads_post": ("threads",),
        "realping": ("realPing",),
    }


def test_local_bridge_registers_and_invokes_direct_tool(monkeypatch):
    from app import mcp_tools

    calls = []

    def fake_invoke(name, arguments, *, config=None):
        calls.append((name, dict(arguments)))
        assert config == {"http": {"timeout": 1}}
        return json.dumps({"ok": True})

    monkeypatch.setattr(mcp_tools, "invoke_tool", fake_invoke)

    async def exercise_bridge():
        server = mcp_tools.maps()
        prepared = mcp_tools.PreparedTools()
        async with mcp_tools.connect(server, 1) as (session, discovered):
            mcp_tools.register_tools(
                server,
                session,
                discovered,
                set(server.tools or ()),
                prepared,
                {},
            )
            route = prepared.routes["maps__googleMaps"]
            result = await route.session.call_tool(
                route.remote_name,
                {"query": "Taipei 101", "operation": "text_search"},
            )
            rendered, failed = mcp_tools.render_result(result, 1000)
        return prepared, rendered, failed

    prepared, rendered, failed = asyncio.run(exercise_bridge())

    assert prepared.function_tools[0]["function"]["name"] == "maps__googleMaps"
    assert calls == [
        ("googleMaps", {"query": "Taipei 101", "operation": "text_search"})
    ]
    assert not failed
    assert json.loads(json.loads(rendered)["result"]) == {"ok": True}
