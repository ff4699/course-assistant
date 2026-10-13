"""Standard-library bootstrap; startup failures remain visible and are logged."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
URL = 'http://127.0.0.1:8765'
MINIMUM = {'fastapi': (0, 115), 'uvicorn': (0, 30), 'pydantic': (2, 10),
           'playwright': (1, 51), 'httpx': (0, 27)}
MAXIMUM = {'fastapi': 1, 'uvicorn': 1, 'pydantic': 3, 'playwright': 2, 'httpx': 1}


class StartupError(Exception):
    pass


def running_assistant(url=URL):
    try:
        # The local service does not require the machine's external proxy.
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url + '/api/health', timeout=1) as response:
            return json.load(response).get('application') == 'cupk-course-assistant'
    except (OSError, ValueError):
        return False


def valid_project(root):
    return all((root / name).is_file() for name in ('requirements.txt', 'assistant/app.py', 'assistant/__main__.py'))


def dependency_check_code():
    return ('from importlib.metadata import version; import re; '
            f'lower={MINIMUM!r}; upper={MAXIMUM!r}; '
            'versions={k:tuple(map(int,re.findall(r"[0-9]+",version(k))[:2])) for k in lower}; '
            'assert all(versions[k]>=lower[k] and versions[k][0]<upper[k] for k in lower); '
            "assert version('playwright')=='1.63.0'; "
            'import fastapi,uvicorn,pydantic,playwright,httpx')


def python_supported(binary):
    try:
        return subprocess.run([str(binary), '-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def probe(binary, code):
    try:
        return subprocess.run([str(binary), '-c', code], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def write(message, log):
    print(message, flush=True)
    log.write(message + '\n')
    log.flush()


def run(command, log, env):
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
    try:
        for line in process.stdout:
            # Private pip indexes can contain user info; keep it out of the log.
            line = re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@', r'\1[hidden]@', line.rstrip())
            write(line, log)
        return process.wait()
    except KeyboardInterrupt:
        try:
            # The console sends Ctrl+C to the child too. Allow normal shutdown
            # to flush the persistent browser Profile before terminating it.
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        return 130
    finally:
        process.stdout.close()


def prepare_python(log, env):
    binary = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if binary.exists() and not python_supported(binary):
        raise StartupError('已有 .venv 的 Python 版本不兼容或环境损坏。请仅移走 .venv 后重试；保留 .state，登录会话和计划都在其中。')
    if not binary.exists():
        write('[1/3] 创建本地 Python 环境（保留 .state）…', log)
        if run([sys.executable, '-m', 'venv', str(ROOT / '.venv')], log, env):
            raise StartupError('创建环境失败。请将完整项目解压到可写文件夹，确认 Python 已安装 venv 组件。')
    if not probe(binary, dependency_check_code()):
        write('[2/3] 安装或更新依赖。首次下载需要联网，下载进度会显示在此窗口。', log)
        result = run([str(binary), '-m', 'pip', 'install', '--disable-pip-version-check',
                      '--timeout', '20', '--retries', '2', '-r', str(ROOT / 'requirements.txt')], log, env)
        if result:
            raise StartupError('依赖安装失败。上方和 startup.log 已记录错误；请检查网络或代理配置后重试。')
    else:
        write('[2/3] 已有依赖可用，直接复用。', log)
    return binary


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser()
    parser.add_argument('--install-browser', action='store_true')
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        print('需要 Python 3.11 或更新版本。')
        return 1
    if not valid_project(ROOT):
        print('项目文件不完整。请先解压整个 ZIP；启动脚本必须与 assistant 文件夹和 requirements.txt 一起保留。')
        return 1
    state = Path(os.environ.get('CUPK_STATE_DIR', str(ROOT / '.state'))).resolve()
    try:
        state.mkdir(parents=True, exist_ok=True)
        with (state / 'startup.log').open('w', encoding='utf-8') as log:
            write('CUPK 启动诊断 ' + datetime.now().isoformat(timespec='seconds'), log)
            write('Python: ' + sys.version.split()[0], log)
            write('日志位置: ' + str(state / 'startup.log'), log)
            env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONUNBUFFERED': '1', 'CUPK_STATE_DIR': str(state)}
            try:
                if not args.install_browser and running_assistant():
                    write('已有助手服务正在运行，复用现有服务。浏览器未打开时，请手动访问 ' + URL, log)
                    webbrowser.open(URL)
                    return 0
                binary = prepare_python(log, env)
                from tools.browser_runtime import bundled_browser
                offline=bundled_browser(ROOT,playwright_version='1.63.0')
                if offline:
                    env['CUPK_CHROMIUM']=offline
                    write('已找到随包提供的匹配版本 Chromium，直接使用。',log)
                if args.install_browser:
                    write('安装学校浏览器组件，下载进度如下。不会清除登录 Profile。', log)
                    if run([str(binary), '-m', 'playwright', 'install', 'chromium'], log, env):
                        raise StartupError('学校浏览器安装失败，请检查下载错误后重试。助手界面和模拟演练仍可运行。')
                    write('学校浏览器组件安装完成，可返回助手连接教务。', log)
                    return 0
                browser_code = ('from pathlib import Path; from playwright.sync_api import sync_playwright; '
                                'p=sync_playwright().start(); found=Path(p.chromium.executable_path).is_file(); '
                                'p.stop(); raise SystemExit(0 if found else 1)')
                if not offline and not probe(binary, browser_code) and not (os.environ.get('CUPK_CHROMIUM') or shutil.which('chromium') or shutil.which('google-chrome')):
                    write('首次启动自动下载 Chromium，下载进度如下；无需安装谷歌浏览器。',log)
                    if run([str(binary),'-m','playwright','install','chromium'],log,env):
                        raise StartupError('浏览器下载未完成。请重试启动或运行「安装学校浏览器.cmd」；Profile和计划保留。')
                write('[3/3] 打开助手界面: ' + URL + '。若网页没有自动打开，请手动输入此地址。', log)
                write('此窗口请保持打开。按 Ctrl+C 停止服务；计划、课表和登录 Profile 会保留。', log)
                result = run([str(binary), '-m', 'assistant', '--open-browser'], log, env)
                if result not in (0, 130):
                    raise StartupError('服务未能启动，请查看上方最后几行错误，或 .state/startup.log。')
                return 0
            except StartupError as error:
                write('启动未完成：' + str(error), log)
                return 1
            except Exception as error:
                write(f'启动未完成（{type(error).__name__}）：{error}', log)
                return 1
    except OSError as error:
        print(f'无法写入启动日志（{type(error).__name__}）。请将完整项目解压到可写文件夹再运行。')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
