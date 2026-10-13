"""Install and exercise the real Windows EXE, with only fictional local data.

Run from the developer environment (pywin32/Pillow), not from the shipped app.
Tests the shared dialog callbacks and the frozen consent gate separately.
It never fabricates an acceptance record or adds a test bypass to the EXE.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

import win32gui
import win32process
from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[1]


def wait_for(check, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.2)
    raise AssertionError('Timed out waiting for verification condition')


def window_for(process, phrase='用户协议'):
    found = []
    def visit(handle, _):
        if win32gui.IsWindowVisible(handle) and phrase in win32gui.GetWindowText(handle):
            if win32process.GetWindowThreadProcessId(handle)[1] == process.pid:
                found.append(handle)
    win32gui.EnumWindows(visit, None)
    return found[0] if found else None


def request(url, path, method='GET', data=None):
    headers = {'X-Course-Client': 'local'}
    body = json.dumps(data).encode() if data is not None else None
    if body is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url + path, data=body, headers=headers, method=method)
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5) as response:
        raw = response.read()
        return json.loads(raw) if 'application/json' in response.headers.get('Content-Type', '') else raw


def healthy(url):
    try:
        return request(url, '/api/health')
    except OSError:
        return None


def close_controller(process):
    handle = window_for(process, '运行控制')
    if handle:
        win32gui.ShowWindow(handle, 9)
        win32gui.PostMessage(handle, 0x0010, 0, 0)
    process.wait(timeout=40)


def shared_dialog_harness(state_dir, base):
    """Exercise the identical shipped dialog's callbacks without a writable bypass.

    Windows desktop input and frozen-process debugger support may be unavailable.
    This source harness uses the real Tk checkbox and button, and checks the digest
    against the document in the installed executable before using that local record.
    """
    script=base/'dialog-harness.py'
    script.write_text(f'''import sys,tkinter as tk
from pathlib import Path
sys.path.insert(0,{str(ROOT)!r})
from tools.desktop_entry import setup_window,request_consent
root=tk.Tk();setup_window(root,760,620)
def act():
 widgets=[]
 def walk(parent):
  for child in parent.winfo_children():widgets.append(child);walk(child)
 walk(root)
 choice=next(w for w in widgets if w.winfo_class()=='TCheckbutton')
 button=next(w for w in widgets if w.winfo_class()=='TButton' and w.cget('text')=='同意并继续')
 assert choice.winfo_height()>15 and button.winfo_height()>15
 assert button.instate(['disabled'])
 button.invoke()
 assert not Path({str(state_dir)!r}).exists()
 choice.invoke()
 assert not button.instate(['disabled'])
 button.invoke()
root.after(250,act)
assert request_consent(root,Path({str(state_dir)!r}))
root.destroy()
''',encoding='utf-8')
    subprocess.run([sys.executable,str(script)],check=True,timeout=20)


def verify(installer):
    base = (ROOT / '.state' / 'exe-validation' / uuid.uuid4().hex[:8]).resolve()
    assert base.is_relative_to(ROOT.resolve())
    base.mkdir(parents=True)
    install_dir = base / '中文安装路径'
    state_dir = base / '个人测试数据'
    report = {'installer': installer.name, 'checks': [], 'clean_machine': False}
    subprocess.run([str(installer.resolve()), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                    '/DIR=' + str(install_dir), '/GROUP=Course EXE 验证', '/TASKS=',
                    '/LOG=' + str(base / 'install.log')], check=True, timeout=180)
    executable = install_dir / 'CourseAssistant.exe'
    assert executable.is_file()
    report['checks'].append('installer_extracts_executable_and_bundled_files')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    env = {**os.environ, 'COURSE_STATE_DIR': str(state_dir), 'PATH': os.path.join(os.environ['WINDIR'], 'System32'),
           'PLAYWRIGHT_BROWSERS_PATH': str(base / 'empty-browser-cache')}
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'COURSE_CHROMIUM', 'COURSE_DESKTOP_INSTANCE'):
        env.pop(key, None)
    processes = []
    def start(*extra):
        process = subprocess.Popen([str(executable), '--port', str(port), *extra], env=env,
                                   cwd=install_dir, creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append(process)
        return process
    try:
        process = start()
        handle = wait_for(lambda: window_for(process))
        bounds = win32gui.GetWindowRect(handle)
        try:
            ImageGrab.grab(window=handle).save(base / 'agreement.png')
        except OSError:
            report['native_screen_capture_available'] = False
        width, height = win32gui.GetClientRect(handle)[2:]
        # The unchecked button is disabled; clicking it cannot initialize any service or store.
        assert process.poll() is None
        assert not healthy(url)
        assert not state_dir.exists()
        win32gui.PostMessage(handle,0x0010,0,0)
        report['checks'].append('unchecked_agreement_blocks_service_browser_and_database')
        process.wait(timeout=10)
        assert not state_dir.exists()
        report['checks'].append('refusing_agreement_exits_without_creating_personal_data')

        shared_dialog_harness(state_dir,base)
        import hashlib
        consent=json.loads((state_dir/'consent.json').read_text())
        shipped=(install_dir/'_internal/docs/USER_AGREEMENT.md').read_text(encoding='utf-8')
        assert consent['document_sha256']==hashlib.sha256(shipped.encode()).hexdigest()
        report['agreement_ui_automation']='shared Tk dialog checkbox/button callbacks; frozen gate and refusal verified separately'
        process = start()
        health = wait_for(lambda: healthy(url))
        assert health['version'] == '0.4.1' and health['desktop_instance']
        record = json.loads((state_dir / 'consent.json').read_text())
        assert set(record) == {'version', 'document_sha256', 'accepted_at'}
        assert record['version'] == '1.1'
        state = request(url, '/api/state')
        assert len(state['catalog']) == 9
        assert not state['session']['browser_running']
        assert state['capabilities']['live_submit'] is False
        assert '免费'.encode() in request(url, '/agreement')
        assert b'MIT' in request(url, '/open-source-notices')
        settings={'targets':[{'class_code':'ART101-01','priority':2},{'class_code':'ART204-01','priority':1}]}
        assert request(url,'/api/seats?mode=demo')['settings']['interval']==5
        request(url,'/api/seats/settings?mode=demo','PUT',settings)
        seats=request(url,'/api/seats/refresh?mode=demo','POST')
        assert seats['targets'][0]['remaining']==2 and seats['recommendations'][0]=='demo:music'
        assert len(request(url,'/api/seats/catalog/refresh?mode=demo','POST')['rows'])==9
        report['checks'].append('frozen_executable_includes_five_second_batch_monitor_and_shared_catalog_snapshot')
        request(url, '/api/plan', 'PUT', {'course_ids': ['demo:ai'], 'retries': 2})
        report['checks'].append('checked_agreement_starts_frozen_app_and_demo_with_school_submission_disabled')
        report['checks'].append('agreement_and_open_source_notices_accessible')

        duplicate = start()
        assert duplicate.wait(timeout=15) == 0
        assert process.poll() is None and healthy(url)
        report['checks'].append('duplicate_launch_reuses_one_service_and_preserves_primary_process')
        close_controller(process)
        assert not healthy(url)
        preferences = json.loads((state_dir / 'ui-profile/Default/Preferences').read_text())
        assert preferences['credentials_enable_service'] is False
        assert preferences['profile']['password_manager_enabled'] is False
        report['checks'].append('normal_exit_stops_service_and_keeps_data_password_saving_disabled')

        process = start()
        wait_for(lambda: healthy(url))
        assert not window_for(process)
        assert request(url, '/api/state')['plan']['course_ids'] == ['demo:ai']
        report['checks'].append('restart_remembers_consent_and_user_plan')
        close_controller(process)

        process = start('--diagnose')
        assert process.wait(timeout=40) == 0
        diagnostic = json.loads((state_dir / 'diagnostic.json').read_text())
        assert diagnostic['ok'] and diagnostic['frozen'] and diagnostic['static_files']
        assert Path(diagnostic['browser']).is_relative_to(install_dir)
        report['runtime'] = {key: diagnostic[key] for key in ('python', 'playwright', 'chromium')}
        report['checks'].append('bundled_python_playwright_node_and_chromium_work_with_system_python_and_browser_cache_unavailable')

        uninstaller = install_dir / 'unins000.exe'
        subprocess.run([str(uninstaller), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'],
                       check=True, timeout=120)
        wait_for(lambda: not executable.exists())
        assert (state_dir / 'consent.json').is_file()
        assert (state_dir / 'assistant.sqlite3').is_file()
        report['checks'].append('uninstall_removes_application_and_preserves_personal_data')
        report['ok'] = True
        (ROOT / 'dist/v0.4.1-exe-validation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))
    except Exception:
        if processes:
            handle = window_for(processes[-1], '课程助手')
            if handle:
                try:
                    ImageGrab.grab(window=handle).save(base / 'failure-screen.png')
                except OSError:
                    pass
        print(json.dumps({'failed_evidence': str(base)}))
        raise
    finally:
        for process in processes:
            if process.poll() is None:
                handle = window_for(process, '课程助手')
                if handle:
                    win32gui.ShowWindow(handle, 9)
                    win32gui.PostMessage(handle, 0x0010, 0, 0)
                try:
                    process.wait(timeout=40)
                except subprocess.TimeoutExpired:
                    # Only the test process is stopped; never any school/source process.
                    process.terminate()
                    process.wait(timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('installer', type=Path)
    verify(parser.parse_args().installer)
