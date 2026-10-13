import argparse
import json
import socket
import threading
import time
import urllib.request
import webbrowser
import uvicorn


def existing_app(url):
    try:
        with urllib.request.urlopen(url + '/api/health', timeout=0.7) as response:
            return json.load(response).get('application') == 'cupk-course-assistant'
    except (OSError, ValueError):
        return False


def open_when_ready(url):
    for _ in range(60):
        if existing_app(url):
            webbrowser.open(url)
            return
        time.sleep(0.25)
    print('界面尚未启动成功，请检查命令窗口中的错误信息。')


def main():
    parser = argparse.ArgumentParser(description="本地 CUPK 选课助手")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true", help="启动后自动打开可视化界面")
    args = parser.parse_args()
    url = f'http://127.0.0.1:{args.port}'
    if existing_app(url):
        print('已有助手服务正在运行，复用现有服务和登录窗口。')
        if args.open_browser:
            webbrowser.open(url)
        return
    with socket.socket() as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(('127.0.0.1', args.port)) == 0:
            parser.exit(1, '端口被其他程序或旧版本占用，请先停止旧版本再启动。\n')
    if args.open_browser:
        threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
    print(f"打开 http://127.0.0.1:{args.port} · 首次运行默认使用模拟选课系统")
    uvicorn.run("assistant.app:app", host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
