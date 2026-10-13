"""Build source/offline Windows ZIPs from public files; exclude private state."""
import argparse
import hashlib
import json
import tomllib
from importlib.metadata import version
from pathlib import Path
import zipfile
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
VERSION=tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
PUBLIC_DIRS={'assistant','tools','tests','handoff','examples','third_party','docs'}
PUBLIC_FILES={'.gitignore','AGENTS.md','README.md','requirements.txt','requirements-build.lock','pyproject.toml','LICENSE','THIRD_PARTY_NOTICES.md',
              'start.cmd','启动助手.bat','安装学校浏览器.cmd'}


def source_files():
    for folder in PUBLIC_DIRS:
        for path in (ROOT/folder).rglob('*'):
            if path.is_file() and not any(part in {'__pycache__','.state','.venv','.git'} for part in path.relative_to(ROOT).parts) and path.suffix not in {'.pyc','.log','.har'}:
                yield path,path.relative_to(ROOT).as_posix()
    for name in PUBLIC_FILES:
        if (ROOT/name).is_file():yield ROOT/name,name


def build(with_browser=False):
    output=ROOT/'dist';output.mkdir(exist_ok=True)
    suffix='windows-offline-browser' if with_browser else 'source'
    target=output/f'course-assistant-v{VERSION}-{suffix}.zip'
    base='course-assistant/'
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=5) as archive:
        for path,name in source_files():archive.write(path,base+name)
        if with_browser:
            with sync_playwright() as p:binary=Path(p.chromium.executable_path)
            if not binary.is_file():raise RuntimeError('请先正常安装对应版本的Playwright Chromium')
            release=binary.parent.parent
            manifest={'playwright_version':version('playwright'),'platform':'windows-x64',
                      'executable':binary.relative_to(release.parent).as_posix(),
                      'executable_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
                      'contains_profile':False}
            archive.writestr(base+'browser/browser-manifest.json',json.dumps(manifest,indent=2))
            for path in release.rglob('*'):
                if path.is_file():archive.write(path,base+'browser/'+path.relative_to(release.parent).as_posix())
    with zipfile.ZipFile(target) as archive:
        if archive.testzip():raise RuntimeError('ZIP完整性检查失败')
        names=archive.namelist()
        if any('/.state/' in n or '/.venv/' in n or 'browser-profile' in n for n in names):raise RuntimeError('归档包含私有路径')
        if with_browser and base+'browser/'+manifest['executable'] not in names:raise RuntimeError('归档缺少浏览器可执行文件')
    print(json.dumps({'artifact':str(target),'files':len(names),'bytes':target.stat().st_size,
                      'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'with_browser':with_browser}))
    return target


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--with-browser',action='store_true')
    args=parser.parse_args();build(args.with_browser)
