"""Reproducible Windows-only public EXE payload and Inno Setup installer."""
import argparse
import hashlib
from importlib.metadata import distribution, distributions, version
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
WORK = ROOT / '.state' / 'build-exe'
RELEASE = ROOT / 'dist'
EXCLUDED = {'pywinauto', 'pywin32', 'comtypes', 'PIL', 'numpy', 'patchright', 'crawl4ai', 'crawlee', 'pip', 'setuptools'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def collect_licenses(target):
    target.mkdir(parents=True, exist_ok=True)
    names = [line.split('==')[0] for line in (ROOT / 'requirements-build.lock').read_text().splitlines()
             if line and not line.startswith('#')]
    collected = []
    for name in names:
        try:
            package = distribution(name)
        except Exception:
            continue
        found = []
        for item in package.files or []:
            leaf = str(item).rsplit('/', 1)[-1].lower()
            if leaf.startswith(('license', 'licence', 'copying', 'notice', 'authors')):
                path = Path(package.locate_file(item))
                if not path.is_file():
                    continue
                destination = target / name / Path(str(item)).name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
                found.append(destination.relative_to(target).as_posix())
        collected.append({'package': name, 'version': package.version, 'license_files': found})
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copy2(python_license, target / 'Python-LICENSE.txt')
    for path in (Path(sys.base_prefix) / 'tcl').glob('*/license.terms'):
        shutil.copy2(path, target / (path.parent.name + '-license.terms'))
    (target / 'DEPENDENCIES.json').write_text(json.dumps(collected, indent=2), encoding='utf-8')
    return collected


def build(iscc, skip_freeze=False):
    if sys.platform != 'win32':
        raise RuntimeError('请在 Windows x64 上构建')
    WORK.mkdir(parents=True, exist_ok=True)
    RELEASE.mkdir(exist_ok=True)
    if not skip_freeze:
        command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
                   '--windowed', '--name', 'CourseAssistant', '--icon', 'NONE',
                   '--distpath', str(WORK / 'payload'), '--workpath', str(WORK / 'pyinstaller'),
                   '--specpath', str(WORK), '--paths', str(ROOT),
                   '--collect-all', 'playwright', '--copy-metadata', 'playwright',
                   '--hidden-import', 'uvicorn.logging', '--hidden-import', 'uvicorn.protocols.http.h11_impl',
                   '--hidden-import', 'uvicorn.lifespan.on',
                   '--add-data', str(ROOT / 'assistant/static') + ':assistant/static',
                   '--add-data', str(ROOT / 'docs/USER_AGREEMENT.md') + ':docs',
                   '--add-data', str(ROOT / 'THIRD_PARTY_NOTICES.md') + ':.',
                   '--add-data', str(ROOT / 'LICENSE') + ':.']
        for package in sorted(EXCLUDED):
            command.extend(['--exclude-module', package.replace('-', '_')])
        command.append(str(ROOT / 'tools/desktop_entry.py'))
        with (WORK / 'pyinstaller-build.log').open('w', encoding='utf-8') as log:
            result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError('PyInstaller 构建失败，详见 .state/build-exe/pyinstaller-build.log')
    payload = WORK / 'payload/CourseAssistant'
    internal = payload / '_internal'
    shutil.copytree(ROOT / 'assistant/static', internal / 'assistant/static', dirs_exist_ok=True)
    shutil.copy2(ROOT / 'docs/USER_AGREEMENT.md', internal / 'docs/USER_AGREEMENT.md')
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT / name, internal / name)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        binary = Path(p.chromium.executable_path)
    release = binary.parent.parent
    browser = internal / 'browser'
    shutil.copytree(release, browser / release.name, dirs_exist_ok=True)
    manifest = {'playwright_version': version('playwright'), 'platform': 'windows-x64',
                'executable': binary.relative_to(release.parent).as_posix(),
                'executable_sha256': digest(binary), 'contains_profile': False}
    (browser / 'browser-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    shutil.copytree(ROOT / 'third_party', internal / 'third_party', dirs_exist_ok=True)
    licenses = collect_licenses(internal / 'licenses')
    inno_license = Path(iscc).resolve().parent / 'License.txt'
    if inno_license.is_file():
        shutil.copy2(inno_license, internal / 'licenses/Inno-Setup-LICENSE.txt')
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT / name, payload / name)
    shutil.copy2(ROOT / 'docs/USER_AGREEMENT.md', payload / '用户使用协议与隐私说明.md')
    instructions = (ROOT / 'docs/WINDOWS_INSTALL.md').read_text(encoding='utf-8')
    (payload / '安装与使用说明.txt').write_text(instructions, encoding='utf-8-sig')
    files = []
    forbidden = {'.state', '.venv', '__pycache__', '.git', 'browser-profile', 'ui-profile'}
    forbidden_names = {'Cookies', 'History', 'Login Data', 'Web Data', 'assistant.sqlite3', 'consent.json', 'desktop-runtime.json'}
    for path in payload.rglob('*'):
        if not path.is_file():
            continue
        relative = path.relative_to(payload)
        if forbidden.intersection(relative.parts) or path.name in forbidden_names or path.suffix in {'.har', '.log'}:
            raise RuntimeError('载荷中存在私有文件：' + str(relative))
        files.append({'path': relative.as_posix(), 'bytes': path.stat().st_size, 'sha256': digest(path)})
    (RELEASE / f'v{VERSION}-exe-payload.json').write_text(json.dumps({'version': VERSION, 'files': files,
        'browser': manifest, 'dependencies': licenses}, indent=2), encoding='utf-8')
    command = [str(iscc), '/Qp', '/DAppVersion=' + VERSION, '/DPayloadDir=' + str(payload),
               '/DReleaseDir=' + str(RELEASE), str(ROOT / 'tools/windows_installer.iss')]
    with (WORK / 'inno-build.log').open('w', encoding='utf-8') as log:
        result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError('安装器构建失败，详见 .state/build-exe/inno-build.log')
    installer = RELEASE / f'Course-Assistant-Setup-{VERSION}-x64.exe'
    info = {'version': VERSION, 'artifact': installer.name, 'bytes': installer.stat().st_size,
            'sha256': digest(installer), 'signed': False, 'with_python': True, 'with_browser': True,
            'agreement_version': '1.1'}
    (RELEASE / f'v{VERSION}-exe-release.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
    (RELEASE / f'{installer.name}.sha256').write_text(info['sha256'] + '  ' + installer.name + '\n', encoding='ascii')
    print(json.dumps(info))
    return installer


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--iscc', type=Path, required=True)
    parser.add_argument('--skip-freeze', action='store_true')
    args = parser.parse_args()
    build(args.iscc, args.skip_freeze)
