#!/usr/bin/env python3
"""Optionally add or remove this user's KDE launcher entry for the panel; no root needed. The Workflow ETA node starts the panel itself."""
import argparse
import os
from pathlib import Path
import shutil
import sys

APP_ID='workflow-eta-panel'
FILES=('workflow_eta_panel.py','panel_model.py','workflow-eta-panel.svg','README.md')

def exec_quote(value):
    # Desktop Entry string escaping is applied before Exec argument unquoting.
    if any(c in value for c in '\n\r\x00'):raise ValueError('Launcher paths cannot contain line breaks or NUL bytes')
    escaped=value.replace('%','%%')
    for char in ('\\','"','`','$'):escaped=escaped.replace(char,'\\'+char)
    escaped=escaped.replace('\\','\\\\')
    return '"'+escaped+'"'

def remove_legacy_copy(data):
    """Delete files an older installer copied to <data>/workflow-eta-panel; unrecognized files are kept."""
    destination=data/APP_ID
    for name in FILES:
        file=destination/name
        if file.is_file() or file.is_symlink():file.unlink()
    try:destination.rmdir()
    except OSError:pass  # preserve unrecognized files and Python bytecode

def install(data_home,python=sys.executable):
    """Optional: add a launcher entry and icon. The panel itself runs in place, from this folder, so it
    stays in step with the Workflow ETA extension (the node's clock button needs no installation)."""
    data=Path(data_home).expanduser().resolve()
    root=Path(__file__).resolve().parent
    remove_legacy_copy(data)
    icon=data/'icons/hicolor/scalable/apps'/f'{APP_ID}.svg';icon.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(root/'workflow-eta-panel.svg',icon)
    desktop=data/'applications'/f'{APP_ID}.desktop';desktop.parent.mkdir(parents=True,exist_ok=True)
    desktop.write_text('[Desktop Entry]\nType=Application\nVersion=1.0\nName=Workflow ETA Panel\nComment=Floating ComfyUI workflow timer\n'
        +'Exec='+exec_quote(os.path.abspath(python))+' '+exec_quote(str(root/'workflow_eta_panel.py'))+'\n'
        +'Icon='+APP_ID+'\nTerminal=false\nCategories=Utility;\nStartupNotify=true\nStartupWMClass='+APP_ID+'\n',encoding='utf-8')
    desktop.chmod(0o644)
    return desktop

def uninstall(data_home):
    data=Path(data_home).expanduser().resolve()
    for file in (data/'applications'/f'{APP_ID}.desktop',data/'icons/hicolor/scalable/apps'/f'{APP_ID}.svg'):
        if file.is_file() or file.is_symlink():file.unlink()
    remove_legacy_copy(data)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--uninstall',action='store_true');args=parser.parse_args()
    data=os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local/share'))
    if not Path(data).is_absolute():raise SystemExit('XDG_DATA_HOME must be an absolute path.')
    if args.uninstall:uninstall(data);print('Panel launcher removed. Your display settings were preserved.');return
    desktop=install(data)
    print('Added the Workflow ETA Panel launcher (the panel runs from this folder). Launch it from the KDE application menu.\nLauncher: '+str(desktop))

if __name__=='__main__':main()
