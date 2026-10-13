"""Resolve the optional matching offline Chromium without touching a Profile."""
import json
import os
from pathlib import Path
from importlib.metadata import version


def bundled_browser(root,playwright_version=None):
    folder=Path(root)/'browser'
    manifest=folder/'browser-manifest.json'
    if not manifest.is_file():return None
    info=json.loads(manifest.read_text(encoding='utf-8'))
    if info.get('platform') == 'windows-x64' and os.name != 'nt':
        return None
    if info.get('playwright_version')!=(playwright_version or version('playwright')):
        return None
    binary=(folder/info['executable']).resolve()
    if not binary.is_relative_to(folder.resolve()):
        raise ValueError('浏览器文件路径无效')
    return str(binary) if binary.is_file() else None
