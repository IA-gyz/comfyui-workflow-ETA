"""The desktop-panel launcher runs the bundled panel with a PySide6-capable interpreter, follows the
requesting server's URL, and reports launch failures instead of claiming success."""
import json
import os
import stat
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow_eta_tracker as t


def fake_python(folder, name, code):
    path = Path(folder) / name
    path.write_text("#!/bin/sh\nexit " + str(code) + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


script = t._workflow_eta_panel_script()
assert script.name == "workflow_eta_panel.py" and script.parent.name == "Workflow_ETA_Panel" and script.is_file(), script

with tempfile.TemporaryDirectory() as root:
    broken = fake_python(root, "venv-python", 1)      # like ComfyUI's venv: no PySide6
    working = fake_python(root, "system-python", 0)   # like /usr/bin/python3 with pacman's pyside6
    assert t._workflow_eta_panel_python([broken]) is None
    assert t._workflow_eta_panel_python([broken, None, working, broken]) == working, "falls through to the next interpreter"
    assert t._workflow_eta_panel_python([os.path.join(root, "missing"), working]) == working

    command, error = t._workflow_eta_panel_command("http://127.0.0.1:8288", [working])
    assert command == [working, str(script), "--url", "http://127.0.0.1:8288"] and error is None, (command, error)
    command, error = t._workflow_eta_panel_command("http://127.0.0.1:8188", [broken])
    assert command is None and "PySide6" in error and "pacman" in error, (command, error)

    with patch.object(t, "_workflow_eta_panel_script", return_value=Path(root) / "gone.py"):
        command, error = t._workflow_eta_panel_command("http://127.0.0.1:8188", [working])
    assert command is None and "missing" in error, (command, error)

    # Launch result: success, an activating second launch (exit 0), a crash with stderr, a long-running panel.
    assert t._workflow_eta_launch_panel([working]) is None
    crash = [sys.executable, "-c", "import sys; sys.stderr.write('boom: no display'); sys.exit(3)"]
    message = t._workflow_eta_launch_panel(crash)
    assert message and "code 3" in message and "boom: no display" in message, message
    assert t._workflow_eta_launch_panel([sys.executable, "-c", "import time; time.sleep(3)"], settle=0.3) is None
    message = t._workflow_eta_launch_panel([os.path.join(root, "missing")])
    assert message and message.startswith("Could not launch the panel"), message

with patch.dict(os.environ, {"VIRTUAL_ENV": "/venv", "PYTHONHOME": "/x", "KEEP": "1"}):
    env = t._workflow_eta_panel_env()
assert "VIRTUAL_ENV" not in env and "PYTHONHOME" not in env and env["KEEP"] == "1"

# ---- loopback gate: aiohttp spells the peer address request.remote (there is no remote_addr)
for remote in ("127.0.0.1", "127.8.9.1", "::1", "::ffff:127.0.0.1", "::1%lo"):
    assert t._workflow_eta_is_loopback(remote), remote
for remote in ("192.168.1.5", "10.0.0.2", "::ffff:192.168.1.5", "2001:db8::1", "", None, "not-an-ip"):
    assert not t._workflow_eta_is_loopback(remote), remote

# ---- the shared colour theme: validated, persisted, pushed, restored (tracker level, no aiohttp needed)
from workflow_eta_history import WorkflowETAHistory


def make_tracker(folder, events):
    history = WorkflowETAHistory(folder + "/h.sqlite")
    server = types.SimpleNamespace(send_sync=lambda event, data, *a, **k: events.append((event, data)))
    tracker = t.WorkflowETATracker(server, history, find_prompt_fn=lambda p: ({}, []), verbose=False, start_thread=False,
                                   theme_path=folder + "/sub/theme.json")
    return tracker, history


with tempfile.TemporaryDirectory() as folder:
    events = []
    tracker, history = make_tracker(folder, events)
    assert tracker.theme == "ocean" and tracker.status_snapshot()["theme"] == "ocean"
    assert tracker.set_theme("forest") is True and tracker.status_snapshot()["theme"] == "forest"
    assert tracker.set_theme("forest") is False, "an unchanged theme is not pushed again"
    assert [e for e in events if e[0] == t.THEME_EVENT] == [(t.THEME_EVENT, {"theme": "forest"})], events
    assert json.loads(Path(folder, "sub", "theme.json").read_text()) == {"theme": "forest"}
    for bad in ("nope", "", None, 5, ["forest"], "FOREST"):
        try:
            tracker.set_theme(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted {bad!r}")
        assert tracker.theme == "forest"
    history.close()

    restarted, history = make_tracker(folder, [])
    assert restarted.theme == "forest", "the last reported theme survives a restart"
    history.close()
    for damaged in ("{not json", '{"theme": "nope"}', "[1]", '{"theme": 5}', ""):
        Path(folder, "sub", "theme.json").write_text(damaged)
        fallback, history = make_tracker(folder, [])
        assert fallback.theme == "ocean", damaged
        history.close()
    no_file, history = make_tracker(folder + "/elsewhere", [])
    assert no_file.theme == "ocean"
    history.close()

# ---- the real route, served by aiohttp (a ComfyUI dependency; skipped where it is not installed)
try:
    import asyncio
    import importlib.util
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
except ImportError:
    web = None
    print("SKIP: aiohttp is not installed; real-route tests not run")

if web is not None:
    with tempfile.TemporaryDirectory() as user_dir:
        routes = web.RouteTableDef()
        pushed = []
        instance = types.SimpleNamespace(routes=routes, send_sync=lambda event, data, *a, **k: pushed.append((event, data)),
                                         add_on_prompt_handler=lambda *a, **k: None)
        sys.modules["server"] = types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=instance))
        sys.modules["folder_paths"] = types.SimpleNamespace(get_user_directory=lambda: user_dir)
        spec = importlib.util.spec_from_file_location("weta_route_test", Path(__file__).resolve().parent / "__init__.py",
                                                      submodule_search_locations=[str(Path(__file__).resolve().parent)])
        package = importlib.util.module_from_spec(spec)
        sys.modules["weta_route_test"] = package
        spec.loader.exec_module(package)  # runs install_workflow_eta(), registering the real routes
        module = sys.modules["weta_route_test.workflow_eta_tracker"]

        async def scenario():
            app = web.Application()
            app.add_routes(routes)
            async with TestClient(TestServer(app)) as client:
                port = client.server.port
                seen = {}

                def command_for(url, candidates=None):
                    seen["url"] = url
                    return ["python3", "panel.py", "--url", url], None

                async def click():
                    response = await client.post("/workflow/eta/open_panel", data=b"{}")
                    return response.status, await response.json(content_type=None)

                # opened: the panel is pointed at the server that received the click
                module._workflow_eta_panel_command = command_for
                module._workflow_eta_launch_panel = lambda command: None
                status, body = await click()
                assert status == 200 and body["ok"] is True, (status, body)
                assert seen["url"] == f"http://127.0.0.1:{port}", seen

                # no interpreter with PySide6: a readable 404, not a crash
                module._workflow_eta_panel_command = lambda url, candidates=None: (None, "needs PySide6 (sudo pacman -S pyside6)")
                status, body = await click()
                assert status == 404 and body == {"ok": False, "error": "needs PySide6 (sudo pacman -S pyside6)"}, (status, body)

                # the panel started and died: its stderr tail reaches the caller
                module._workflow_eta_panel_command = command_for
                module._workflow_eta_launch_panel = lambda command: "The panel exited with code 3: boom"
                status, body = await click()
                assert status == 500 and body["error"] == "The panel exited with code 3: boom", (status, body)

                # an unexpected failure still answers with JSON the node can show, never a bare 500
                def explode(url, candidates=None):
                    raise RuntimeError("kaboom")
                module._workflow_eta_panel_command = explode
                status, body = await click()
                assert status == 500 and body["ok"] is False and "kaboom" in body["error"], (status, body)

                # remote machines are refused
                real_is_loopback = module._workflow_eta_is_loopback
                module._workflow_eta_is_loopback = lambda remote: False
                status, body = await click()
                assert status == 403 and body["ok"] is False, (status, body)
                module._workflow_eta_is_loopback = real_is_loopback

                # the status endpoint carries the contract fields the panel checks
                response = await client.get("/workflow/eta/status")
                payload = await response.json()
                assert response.status == 200 and payload["schema"] == module.STATUS_SCHEMA and payload["version"] == module.WORKFLOW_ETA_VERSION, payload
                assert payload["theme"] == "ocean"

                # the node reports its theme: stored, saved, pushed live, served in the status and the browser page
                async def report(**kwargs):
                    response = await client.post("/workflow/eta/theme", **kwargs)
                    return response.status, await response.json(content_type=None)

                assert await report(json={"theme": "forest"}) == (200, {"ok": True, "theme": "forest", "changed": True})
                assert await report(json={"theme": "forest"}) == (200, {"ok": True, "theme": "forest", "changed": False})
                assert [d for e, d in pushed if e == "workflow.eta.theme"] == [{"theme": "forest"}], pushed
                response = await client.get("/workflow/eta/status")
                assert (await response.json())["theme"] == "forest"
                assert json.loads(Path(package._workflow_eta_tracker.theme_path).read_text()) == {"theme": "forest"}
                for bad in ({"theme": "nope"}, {"theme": 5}, {"theme": None}, {}, [1], "forest"):
                    status, body = await report(json=bad)
                    assert status == 400 and body["ok"] is False and "theme must be one of" in body["error"] and "forest" in body["error"], (bad, status, body)
                status, body = await report(data=b"not json at all")
                assert status == 400, (status, body)
                assert package._workflow_eta_tracker.theme == "forest", "a rejected report changes nothing"
                response = await client.get("/workflow/eta/panel")
                page = await response.text()
                assert response.status == 200 and "__WORKFLOW_ETA_THEMES__" not in page
                assert json.loads(page.split("const THEMES=")[1].split(";let theme")[0]) == module.WORKFLOW_ETA_THEMES, "the page embeds the shared palettes"

                # the status endpoint: complete for the node, compact for the panels (they never read inspector data or logs)
                live = package._workflow_eta_tracker
                live.last_payload = dict(state="running", elapsed=1.0, remaining=2.0, prompt_id="px",
                                         watched=dict(full=True, prompt_id="px", rows=[{"id": "1"}], nodes={}, previous_nodes={}, baseline_prompt=None))
                everything = await (await client.get("/workflow/eta/status")).json()
                assert everything["watched"]["rows"] == [{"id": "1"}] and "logs" in everything and "compact" not in everything
                for flag in ("1", "true"):
                    small = await (await client.get("/workflow/eta/status?compact=" + flag)).json()
                    assert "watched" not in small and "logs" not in small and small["compact"] is True, (flag, list(small))
                    assert small["state"] == "running" and small["schema"] == module.STATUS_SCHEMA and small["theme"] == "forest", small
                for flag in ("0", "false", "", "yes"):
                    assert "watched" in await (await client.get("/workflow/eta/status?compact=" + flag)).json(), flag
                live.last_payload = None

                # the real install path stamps diagnostics records with the extension version
                assert live.diagnostics is not None and live.diagnostics.extension_version == module.WORKFLOW_ETA_VERSION, live.diagnostics

        asyncio.run(scenario())
        package._workflow_eta_tracker.close()
    print("PASS: real aiohttp route: opens for this server, readable 404/500 reasons, no bare 500, remote refused, status carries schema and theme; theme route validates, persists and pushes")

print("PASS: panel runs bundled, picks a PySide6 interpreter, follows the server URL and reports launch failures")
