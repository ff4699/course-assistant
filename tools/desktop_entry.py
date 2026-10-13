"""PyInstaller entry: consent first, bundled UI browser, graceful local service."""
import argparse
import asyncio
import ctypes
import hashlib
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
import urllib.request

from assistant.consent import (RESOURCE_ROOT, agreement_text, has_consent,
                              save_consent, disable_password_storage)
from tools.browser_runtime import bundled_browser

TITLE = 'CUPK 选课助手'


def local_health(url):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + '/api/health', timeout=0.5) as response:
            return json.load(response)
    except (OSError, ValueError):
        return {}


class InstanceLock:
    def __init__(self, identity):
        library = ctypes.WinDLL('kernel32', use_last_error=True)
        library.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        library.CreateMutexW.restype = ctypes.c_void_p
        library.CloseHandle.argtypes = [ctypes.c_void_p]
        self.library = library
        self.handle = library.CreateMutexW(None, False, 'Local\\CUPKCourseAssistant-' + identity)
        if not self.handle:
            raise OSError('无法创建单实例锁')
        self.duplicate = ctypes.get_last_error() == 183

    def close(self):
        if self.handle:
            self.library.CloseHandle(self.handle)
            self.handle = None


def setup_window(root, width, height):
    root.title(TITLE)
    left = max(0, (root.winfo_screenwidth() - width) // 2)
    top = max(0, (root.winfo_screenheight() - height) // 2)
    root.geometry(f'{width}x{height}+{left}+{top}')
    root.option_add('*Font', ('Microsoft YaHei UI', 10))
    style = ttk.Style(root)
    # Use consistent metrics across Windows themes and display scaling settings.
    style.theme_use('clam')
    style.configure('TButton', font=('Microsoft YaHei UI', 10), padding=(12, 6))
    style.configure('TCheckbutton', font=('Microsoft YaHei UI', 10))
    icon = RESOURCE_ROOT / 'assistant' / 'static' / 'app.ico'
    if icon.exists():
        root.iconbitmap(str(icon))


def request_consent(root, state_dir):
    root.title(TITLE + ' — 用户协议')
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='欢迎使用 CUPK 选课助手', font=('Microsoft YaHei UI', 16, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='免费本地工具 · 请阅读使用协议与隐私说明', foreground='#606873').pack(anchor='w', pady=(5, 12))
    body = ttk.Frame(frame)
    body.pack(fill='both', expand=True)
    text = tk.Text(body, height=1, wrap='word', relief='solid', borderwidth=1, padx=12, pady=12,
                   background='#ffffff', foreground='#1d2430', font=('Microsoft YaHei UI', 10))
    scroll = ttk.Scrollbar(body, command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right', fill='y')
    text.pack(side='left', fill='both', expand=True)
    text.insert('1.0', agreement_text())
    text.configure(state='disabled')
    checked = tk.BooleanVar(value=False)
    choice = ttk.Checkbutton(frame, text='我已阅读并同意《用户使用协议与隐私说明》', variable=checked)
    choice.pack(anchor='w', pady=(14, 8))
    buttons = ttk.Frame(frame)
    buttons.pack(fill='x')
    accepted = False

    def finish():
        nonlocal accepted
        if not checked.get():
            return
        try:
            save_consent(state_dir)
        except OSError:
            messagebox.showerror(TITLE, '无法保存同意记录，请检查用户数据目录的写入权限。', parent=root)
            return
        accepted = True
        root.quit()

    def reject():
        root.quit()

    continue_button = ttk.Button(buttons, text='同意并继续', command=finish, state='disabled')
    continue_button.pack(side='right', padx=(10, 0))
    ttk.Button(buttons, text='不同意并退出', command=reject).pack(side='right')
    checked.trace_add('write', lambda *_: continue_button.configure(state='normal' if checked.get() else 'disabled'))
    root.protocol('WM_DELETE_WINDOW', reject)
    root.mainloop()
    frame.destroy()
    return accepted


def launch_ui(binary, state_dir, url, prepare=False):
    profile = state_dir / 'ui-profile'
    if prepare:
        disable_password_storage(profile)
    return subprocess.Popen([binary, '--app=' + url, '--user-data-dir=' + str(profile),
                             '--no-first-run', '--no-default-browser-check', '--disable-background-mode',
                             '--disable-sync', '--disable-component-update',
                             '--disable-features=PasswordManagerOnboarding,PasswordLeakDetection'],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW)


def diagnose(binary, state_dir):
    from playwright.sync_api import sync_playwright
    # No school network or existing login context is used for this explicit diagnostic.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='cupk-browser-check-', dir=state_dir) as temporary:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(temporary, executable_path=binary, headless=True)
            page = context.new_page()
            page.set_content('<title>CUPK offline browser check</title><p>ok</p>')
            browser_version = context.browser.version
            assert page.title() == 'CUPK offline browser check'
            context.close()
    from importlib.metadata import version
    report = {'ok': True, 'frozen': bool(getattr(sys, 'frozen', False)),
              'python': sys.version.split()[0], 'playwright': version('playwright'),
              'chromium': browser_version, 'browser': binary,
              'static_files': (RESOURCE_ROOT / 'assistant/static/index.html').is_file()}
    (state_dir / 'diagnostic.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


def close_ui(process):
    if not process or process.poll() is not None:
        return
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def close_window(handle, _):
        owner = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value == process.pid:
            ctypes.windll.user32.PostMessageW(handle, 0x0010, 0, 0)
        return True

    ctypes.windll.user32.EnumWindows(callback_type(close_window), 0)


def main():
    # A windowed executable has no console streams; libraries still expect writable handles.
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, 'w', encoding='utf-8'))
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--diagnose', action='store_true')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65525:
        return 2
    state_dir = Path(os.environ.get('CUPK_STATE_DIR') or
                     (Path(os.environ['LOCALAPPDATA']) / 'CUPKCourseAssistant' / 'data')).resolve()
    identity = hashlib.sha256(str(state_dir).casefold().encode()).hexdigest()[:24]
    root = tk.Tk()
    setup_window(root, 760, 620)
    lock = None
    try:
        default_data = (Path(os.environ['LOCALAPPDATA']) / 'CUPKCourseAssistant/data').resolve()
        lock = InstanceLock('Desktop' if state_dir == default_data else identity)
        if lock.duplicate:
            # Reuse the already-consented EXE instance; never attach to the source service.
            root.withdraw()
            for _ in range(40):
                try:
                    runtime = json.loads((state_dir / 'desktop-runtime.json').read_text(encoding='utf-8'))
                    port = runtime['port']
                    if not isinstance(port, int) or not 1024 <= port <= 65535:
                        break
                    url = f'http://127.0.0.1:{port}'
                    if has_consent(state_dir) and local_health(url).get('desktop_instance') == identity:
                        binary = bundled_browser(RESOURCE_ROOT)
                        if binary:
                            launch_ui(binary, state_dir, url)
                        return 0
                except (OSError, ValueError, KeyError):
                    pass
                time.sleep(0.15)
            messagebox.showinfo(TITLE, '助手已在运行或等待确认协议，请切换到已有窗口。', parent=root)
            return 0
        if not has_consent(state_dir) and not request_consent(root, state_dir):
            return 0
        state_dir.mkdir(parents=True, exist_ok=True)
        binary = bundled_browser(RESOURCE_ROOT)
        if not binary:
            messagebox.showerror(TITLE, '内置浏览器文件缺失或版本不匹配，请重新安装完整程序。', parent=root)
            return 1
        os.environ['CUPK_STATE_DIR'] = str(state_dir)
        os.environ['CUPK_CHROMIUM'] = binary
        os.environ['CUPK_DESKTOP_INSTANCE'] = identity
        import uvicorn
        from assistant.app import app
        # External Chromium and Playwright Node must not inherit PyInstaller's DLL search folder.
        ctypes.windll.kernel32.SetDllDirectoryW(None)
        if args.diagnose:
            root.withdraw()
            diagnose(binary, state_dir)
            return 0
        listener = socket.socket()
        for port in range(args.port, args.port + 10):
            try:
                listener.bind(('127.0.0.1', port))
                break
            except OSError:
                continue
        else:
            listener.close()
            messagebox.showerror(TITLE, '本地端口已被占用，请稍后重试。', parent=root)
            return 1
        url = f'http://127.0.0.1:{port}'
        config = uvicorn.Config(app, host='127.0.0.1', port=port, loop='none',
                                log_config=None, access_log=False, lifespan='on')
        server = uvicorn.Server(config)
        errors = queue.Queue()

        def serve():
            try:
                asyncio.run(server.serve(sockets=[listener]))
            except BaseException as error:
                errors.put(type(error).__name__)

        worker = threading.Thread(target=serve, name='cupk-local-service')
        worker.start()
        root.resizable(False, False)
        setup_window(root, 410, 230)
        root.title(TITLE + ' — 运行控制')
        panel = ttk.Frame(root, padding=22)
        panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='CUPK 选课助手', font=('Microsoft YaHei UI', 16, 'bold')).pack(anchor='w')
        status = tk.StringVar(value='正在启动本地服务…')
        ttk.Label(panel, textvariable=status, wraplength=360).pack(anchor='w', pady=14)
        row = ttk.Frame(panel)
        row.pack(fill='x')
        ui_process = None
        closing = False
        started_at = time.monotonic()
        runtime_file = state_dir / 'desktop-runtime.json'

        def open_ui():
            nonlocal ui_process
            reopened = launch_ui(binary, state_dir, url)
            if ui_process is None or ui_process.poll() is not None:
                ui_process = reopened
            root.iconify()

        def stop():
            nonlocal closing
            closing = True
            server.should_exit = True
            close_ui(ui_process)
            status.set('正在正常退出，保存计划和登录会话…')
            root.deiconify()

        ttk.Button(row, text='重新打开界面', command=open_ui).pack(side='left')
        ttk.Button(row, text='退出程序', command=stop).pack(side='right')
        root.protocol('WM_DELETE_WINDOW', stop)

        def poll():
            nonlocal ui_process
            if closing:
                if not worker.is_alive():
                    runtime_file.unlink(missing_ok=True)
                    root.quit()
                    return
            elif not errors.empty() or not worker.is_alive():
                server.should_exit = True
                messagebox.showerror(TITLE, '本地服务启动或运行失败，请检查安装文件是否完整。', parent=root)
                root.quit()
                return
            elif server.started and ui_process is None:
                disable_password_storage(state_dir / 'ui-profile')
                runtime_file.write_text(json.dumps({'port': port}), encoding='utf-8')
                status.set('界面已打开。关闭界面会正常退出助手。')
                open_ui()
            elif ui_process and ui_process.poll() is not None:
                stop()
            elif not server.started and time.monotonic() - started_at > 30:
                stop()
                messagebox.showerror(TITLE, '启动超过30秒，请检查磁盘和安装文件。', parent=root)
            root.after(250, poll)

        root.after(100, poll)
        try:
            root.mainloop()
        finally:
            server.should_exit = True
            worker.join(timeout=30)
            listener.close()
            runtime_file.unlink(missing_ok=True)
        return 0 if not worker.is_alive() else 1
    except Exception as error:
        # Do not log upstream exceptions, request arguments or authentication material.
        messagebox.showerror(TITLE, f'启动未完成（{type(error).__name__}），请检查完整安装和用户目录权限。', parent=root)
        return 1
    finally:
        if lock:
            lock.close()
        root.destroy()


if __name__ == '__main__':
    raise SystemExit(main())
