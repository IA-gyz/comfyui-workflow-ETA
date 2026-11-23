"""The "Copy to comfyui.log" choice is saved and survives a ComfyUI restart.

It used to live only in the running server (reset to the WORKFLOW_ETA_VERBOSE default, normally ON, at every start) while the
node kept its own copy in the workflow and overwrote it with whatever the server reported, so the box was always checked."""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import types
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))
from workflow_eta_history import WorkflowETAHistory
import workflow_eta_tracker as t


def make(folder, verbose, name="copy_to_log.json", events=None):
    history = WorkflowETAHistory(folder + "/h.sqlite")
    server = types.SimpleNamespace(send_sync=lambda *args: (events if events is not None else []).append(args))
    tracker = t.WorkflowETATracker(server, history, find_prompt_fn=lambda pid: ({"1": {"class_type": "Example", "inputs": {}}}, ["1"]),
                                   clock=lambda: 100.0, wall=lambda: 100.0, verbose=verbose, start_thread=False,
                                   copy_log_path=(folder + "/sub/" + name) if name else None)
    return tracker, history


with tempfile.TemporaryDirectory() as folder:
    saved_file = Path(folder, "sub", "copy_to_log.json")

    # no saved choice: the environment default (the `verbose` argument) applies
    for default in (True, False):
        tracker, history = make(folder, default)
        assert tracker.verbose is default and tracker.status_snapshot()["copy_to_log"] is default
        history.close()
    assert not saved_file.exists(), "nothing is written until the user chooses"

    # choosing saves, reports the value and is remembered by the next start
    tracker, history = make(folder, True)
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        assert tracker.set_copy_to_log(False) is False
    assert tracker.verbose is False and tracker.status_snapshot()["copy_to_log"] is False
    assert json.loads(saved_file.read_text()) == {"copy_to_log": False}
    assert printed.getvalue() == "", "switching OFF prints nothing (the entry stays in the node's buffer)"
    assert any("copy to comfyui.log disabled" in e["message"] for e in tracker.status_snapshot()["logs"])
    history.close()

    # a saved choice wins over the default, in BOTH directions
    for default in (True, False):
        restarted, history = make(folder, default)
        assert restarted.verbose is False and restarted.status_snapshot()["copy_to_log"] is False, f"saved OFF must beat default {default}"
        history.close()
    tracker, history = make(folder, False)
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        assert tracker.set_copy_to_log(True) is True
    assert "[workflow-eta] copy to comfyui.log enabled" in printed.getvalue(), printed.getvalue()
    history.close()
    for default in (True, False):
        restarted, history = make(folder, default)
        assert restarted.verbose is True, f"saved ON must beat default {default}"
        history.close()

    # only real booleans are accepted; a rejected value changes nothing
    tracker, history = make(folder, True)
    before = saved_file.read_text()
    for bad in ("false", "true", 0, 1, None, [], {}, ""):
        try:
            tracker.set_copy_to_log(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted {bad!r}")
        assert tracker.verbose is True and saved_file.read_text() == before
    history.close()

    # a damaged or foreign file is ignored: the default applies
    for damaged in ("{not json", "[]", "null", '{"copy_to_log": "no"}', '{"copy_to_log": 0}', '{"other": true}', ""):
        saved_file.write_text(damaged)
        for default in (True, False):
            tracker, history = make(folder, default)
            assert tracker.verbose is default, (damaged, default, tracker.verbose)
            history.close()

    # an unwritable location never breaks the switch: it works for this session and says why it was not saved
    blocker = Path(folder, "blocker")
    blocker.write_text("a file where a folder is needed")
    tracker, history = make(folder, True, name=None)
    tracker.copy_log_path = str(blocker / "copy_to_log.json")
    assert tracker.set_copy_to_log(False) is False and tracker.verbose is False
    assert any("could not save the Copy to comfyui.log choice" in e["message"] for e in tracker.status_snapshot()["logs"])
    history.close()

    # with no path configured nothing is written anywhere
    tracker, history = make(folder, True, name=None)
    assert tracker.set_copy_to_log(False) is False and tracker.copy_log_path is None
    history.close()

# ---- the real routes across a simulated ComfyUI restart (aiohttp is a ComfyUI dependency; skipped where it is not installed)
try:
    import asyncio
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
except ImportError:
    web = None
    print("SKIP: aiohttp is not installed; restart simulation not run")

if web is not None:
    user_dir = tempfile.mkdtemp(prefix="copylog-test-")

    def boot(name, verbose_env=None):
        """One ComfyUI start: a fresh server object and a fresh import of the extension, same user folder."""
        if verbose_env is None:
            os.environ.pop("WORKFLOW_ETA_VERBOSE", None)
        else:
            os.environ["WORKFLOW_ETA_VERBOSE"] = verbose_env
        routes = web.RouteTableDef()
        instance = types.SimpleNamespace(routes=routes, send_sync=lambda *a, **k: None, add_on_prompt_handler=lambda *a, **k: None)
        sys.modules["server"] = types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=instance))
        sys.modules["folder_paths"] = types.SimpleNamespace(get_user_directory=lambda: user_dir)
        for key in [k for k in sys.modules if k.startswith("weta_copylog")]:
            del sys.modules[key]
        spec = importlib.util.spec_from_file_location(name, root / "__init__.py", submodule_search_locations=[str(root)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[name] = package
        spec.loader.exec_module(package)
        return routes, package

    async def session(name, verbose_env, actions):
        routes, package = boot(name, verbose_env)
        app = web.Application()
        app.add_routes(routes)
        async with TestClient(TestServer(app)) as client:
            async def status():
                return (await (await client.get("/workflow/eta/status")).json())["copy_to_log"]

            async def post(**kwargs):
                response = await client.post("/workflow/eta/copy_log", **kwargs)
                return response.status, await response.json(content_type=None)
            return await actions(status, post)

    async def scenario():
        async def first(status, post):
            assert await status() is True, "fresh install: the environment default (ON)"
            assert await post(json={"enabled": False}) == (200, {"ok": True, "copy_to_log": False})
            assert await status() is False
        await session("weta_copylog_1", None, first)

        async def restarted_off(status, post):
            assert await status() is False, "the choice survived the restart (this was the bug: it came back ON)"
            for bad in ({}, {"enabled": "yes"}, {"enabled": 1}, [True], "true"):
                code, body = await post(json=bad)
                assert code == 400 and body["ok"] is False, (bad, code, body)
            code, _ = await post(data=b"not json")
            assert code == 400
            assert await status() is False, "rejected requests change nothing"
            assert await post(json={"enabled": True}) == (200, {"ok": True, "copy_to_log": True})
        await session("weta_copylog_2", None, restarted_off)

        async def restarted_on(status, post):
            assert await status() is True, "ON is remembered as well"
        await session("weta_copylog_3", None, restarted_on)

        async def env_quiet_but_saved_wins(status, post):
            assert await status() is True, "a saved choice beats WORKFLOW_ETA_VERBOSE=0"
            await post(json={"enabled": False})
        await session("weta_copylog_4", "0", env_quiet_but_saved_wins)

        async def env_loud_but_saved_wins(status, post):
            assert await status() is False, "a saved choice beats the default ON"
        await session("weta_copylog_5", "1", env_loud_but_saved_wins)

    try:
        asyncio.run(scenario())
    finally:
        os.environ.pop("WORKFLOW_ETA_VERBOSE", None)

print("PASS: Copy to comfyui.log is saved, beats the environment default in both directions, rejects non-booleans, "
      "survives damaged or unwritable files, and survives a simulated ComfyUI restart through the real routes")
