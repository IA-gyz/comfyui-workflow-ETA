#!/usr/bin/env python3
"""Install or remove this user's panel and KDE application launcher; no root needed."""
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

def install(data_home,python=sys.executable):
    data=Path(data_home).expanduser().resolve();destination=data/APP_ID
    destination.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parent
    for name in FILES:
        source=root/name;target=destination/name
        if source.resolve()!=target.resolve():shutil.copy2(source,target)
    icon=data/'icons/hicolor/scalable/apps'/f'{APP_ID}.svg';icon.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(root/'workflow-eta-panel.svg',icon)
    desktop=data/'applications'/f'{APP_ID}.desktop';desktop.parent.mkdir(parents=True,exist_ok=True)
    desktop.write_text('[Desktop Entry]\nType=Application\nVersion=1.0\nName=Workflow ETA Panel\nComment=Floating ComfyUI workflow timer\n'
        +'Exec='+exec_quote(os.path.abspath(python))+' '+exec_quote(str(destination/'workflow_eta_panel.py'))+'\n'
        +'Icon='+APP_ID+'\nTerminal=false\nCategories=Utility;\nStartupNotify=true\nStartupWMClass='+APP_ID+'\n',encoding='utf-8')
    desktop.chmod(0o644)
    return desktop

def uninstall(data_home):
    data=Path(data_home).expanduser().resolve()
    for file in (data/'applications'/f'{APP_ID}.desktop',data/'icons/hicolor/scalable/apps'/f'{APP_ID}.svg'):
        if file.is_file() or file.is_symlink():file.unlink()
    destination=data/APP_ID
    for name in FILES:
        file=destination/name
        if file.is_file() or file.is_symlink():file.unlink()
    try:destination.rmdir()
    except OSError:pass  # preserve unrecognized files and Python bytecode

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--uninstall',action='store_true');args=parser.parse_args()
    data=os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local/share'))
    if not Path(data).is_absolute():raise SystemExit('XDG_DATA_HOME must be an absolute path.')
    if args.uninstall:uninstall(data);print('Panel launcher removed. Your display settings were preserved.');return
    desktop=install(data)
    print('Installed Workflow ETA Panel. Launch it from the KDE application menu.\nLauncher: '+str(desktop))

if __name__=='__main__':main()
