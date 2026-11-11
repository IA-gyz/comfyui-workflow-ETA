# Workflow ETA Panel v0.1.4

A movable desktop companion for the Workflow ETA ComfyUI extension, designed for KDE Plasma and Wayland. The panel reads the same status and timing history used by the ComfyUI node; it does not create another estimator or start/stop generations.

## Install on CachyOS / Arch Linux

Install the system Qt Python binding if you do not already have it:

```bash
sudo pacman -S pyside6
```

Extract this ZIP, open a terminal in its `Workflow_ETA_Panel` folder and run:

```bash
python3 install.py
```

Find **Workflow ETA Panel** in KDE's application launcher. You can pin its running icon to the task manager. The installer copies the app to your user application-data directory and creates a matching icon and desktop entry; it does not require sudo. Use the same Python interpreter for installation and execution, with PySide6 available to it. No package installation is performed by the installer.

To run without installation:

```bash
python3 workflow_eta_panel.py
```

ComfyUI must have Workflow ETA installed and enabled. The latest available extension package is v0.2.13; no extension update is required for this panel. Default server: `http://127.0.0.1:8188`.

## Controls and display

- **Large timer:** full/provisional remaining time, followed by final runtime in yellow when completed.
- **Below it:** estimated workflow progress, elapsed time and expected finish.
- **Expanded view:** expected total, full start/finish date and time, current node, completed/cached counts and prediction/uncertainty explanation.
- **↑:** keep the window above other windows. This is a window-manager hint, subject to KDE policy.
- **▾ / ▸:** switch expanded/compact views.
- **⚙:** server URL, refresh interval (1–10 seconds), seven themes, Scale UI slider with percentage input (25–200%, saved between launches), opacity (40–100%), keep-above and native title-bar preferences.
- **Drag:** use the native title bar or the WORKFLOW ETA heading. Resize from the window edge or bottom-right grip.
- **Open ComfyUI:** open the configured server in your default browser.

Only one panel runs per user. Launching again requests activation of the existing window. Closing the panel exits it; no tray icon, service or hidden polling process is left behind. Demo mode is a separate instance and uses simulated data:

```bash
python3 workflow_eta_panel.py --demo
```

You can set the URL at launch (close an existing panel first) or use Settings:

```bash
python3 workflow_eta_panel.py --url http://127.0.0.1:8188 --compact
```

## Estimates and failure states

The app polls `GET /workflow/eta/status` asynchronously. Requests never overlap, have a four-second deadline, and accept at most 8 MiB. HTTP errors, missing extensions, malformed data and disconnects appear in the panel. It retries automatically. Credentials in URLs are rejected; authenticated browser/proxy sessions are not imported.

Between fresh responses, the display interpolates the running countdown and elapsed time using a monotonic clock. It freezes the large timer on connection failure or when a running server snapshot stops updating for at least ten seconds. A long first sampling step is not itself stale: a working Workflow ETA server continues to update elapsed time. Completed runtimes never count down. Unpriced nodes and provisional estimates remain explicit; a subtotal is never silently substituted for a missing full projection. The progress bar is an estimate of whole-workflow progress, not the sampler's step-completion percentage.

Settings, compact mode and size survive restarts. On X11, window geometry is also saved. **Wayland placement belongs to KDE:** use native dragging and KDE window rules to remember a fixed position, appear on all desktops, or set compositor-specific behavior. The app does not force screen coordinates on Wayland. Display times use your desktop's local timezone.

The panel is designed to keep the last completed generation visible until another run starts. Offline status is always identified; it does not pretend an old estimate is live. It can work with other desktops, but KDE Wayland movement, pinning and opacity still need verification on your machine.

## Remove

From the extracted folder:

```bash
python3 install.py --uninstall
```

The launcher and installed application files are removed. Display settings are retained at the standard Qt settings location, normally `~/.config/WorkflowETA/workflow-eta-panel.conf`. Your ComfyUI history and diagnostic files are never modified.

## Validation

Run the tests with PySide6 available:

```bash
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s . -p 'test_*.py' -v
```

Tests cover estimate handling, partial/completed/aborted states, stale snapshots versus long steps, URL validation, launcher generation, single-instance locks, themes, compact mode/settings, asynchronous HTTP polling, deadlines, reconnection, reply-size limits and canceled requests. Headless Qt rendering is checked; this does not substitute for a real KDE Wayland session.

Implementation references: [Qt asynchronous networking](https://doc.qt.io/qtforpython-6/PySide6/QtNetwork/QNetworkAccessManager.html), [native window movement](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QWindow.html), [desktop launcher format](https://specifications.freedesktop.org/desktop-entry/1.2/exec-variables.html), [Arch pyside6](https://archlinux.org/packages/extra/x86_64/pyside6/).

UI scaling adjusts fonts, spacing, padding, and panel size together. Native KDE title bars follow your desktop settings.

On KDE Wayland, Keep above now uses an asynchronous KWin scripting request scoped to this panel process. The script is unloaded on exit. If KWin rejects the request, a warning explains the manual KDE alternative (Alt+F3 → More Actions → Keep Above Others). Native stacking requires verification on KDE Wayland.

The panel now fits its height to content on startup and workflow updates, correcting oversized saved heights. Wrapped node and diagnostic text has a bounded height, with full content in tooltips.
