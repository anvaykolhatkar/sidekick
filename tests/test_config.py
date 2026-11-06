"""Checks the settings that differ between a desktop and a container. No API calls."""

import pytest

import app
from sidekick_tools import mcp_connections, our_tools


def tool_names(headless):
    return {tool.name for tool in our_tools(headless)}


def test_headed_browser_uses_defaults():
    args = mcp_connections("sandbox", headless=False)["playwright"]["args"]
    assert "--headless" not in args
    assert "--no-sandbox" not in args


def test_headless_browser_can_run_in_a_container():
    args = mcp_connections("sandbox", headless=True)["playwright"]["args"]
    assert "--headless" in args
    assert args[args.index("--browser") + 1] == "chromium"
    assert "--no-sandbox" in args


def test_servers_are_never_downloaded_at_runtime():
    for server in mcp_connections("sandbox", headless=True).values():
        assert server["args"][0] == "--no-install"


def test_human_help_needs_a_visible_browser():
    assert "request_human_help" in tool_names(headless=False)
    assert "request_human_help" not in tool_names(headless=True)


def test_no_login_is_fine_locally(monkeypatch):
    for key in ("SIDEKICK_USERNAME", "SIDEKICK_PASSWORD", "SPACE_ID"):
        monkeypatch.delenv(key, raising=False)
    assert app.login() is None


def test_space_refuses_to_start_without_a_login(monkeypatch):
    monkeypatch.delenv("SIDEKICK_USERNAME", raising=False)
    monkeypatch.delenv("SIDEKICK_PASSWORD", raising=False)
    monkeypatch.setenv("SPACE_ID", "someone/sidekick")
    with pytest.raises(SystemExit):
        app.login()


def test_space_starts_with_a_login(monkeypatch):
    monkeypatch.setenv("SIDEKICK_USERNAME", "me")
    monkeypatch.setenv("SIDEKICK_PASSWORD", "secret")
    monkeypatch.setenv("SPACE_ID", "someone/sidekick")
    assert app.login() == ("me", "secret")


def test_approve_button_stays_on_the_page_while_hidden():
    # visible=False removes it from the page for good, which strands a paused run
    assert app.approve_button_update(False)["visible"] == "hidden"
    assert app.approve_button_update(True)["visible"] is True


def test_plan_text_is_escaped():
    html = app.render_todos([{"status": "pending", "content": "<script>"}])
    assert "<script>" not in html
