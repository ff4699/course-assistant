"""Check a public Windows release and launch its bundled browser on a local fixture."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tempfile
import sys
import zipfile
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.browser_runtime import bundled_browser


def verify(path):
    state=ROOT/'.state'
    state.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=state,prefix='release-check-') as directory:
        target=Path(directory).resolve()
        # Validate the final temporary target before cleanup can recursively remove it.
        assert target.is_relative_to(state.resolve()) and target!=state.resolve()
        with zipfile.ZipFile(path) as archive:
            assert archive.testzip() is None
            for name in archive.namelist():
                relative=PurePosixPath(name)
                assert not relative.is_absolute() and '..' not in relative.parts
                assert not any(part in {'.state','.venv','.git','browser-profile'} for part in relative.parts)
            archive.extractall(target)
        project=target/'cupk-course-assistant'
        manifest=json.loads((project/'browser/browser-manifest.json').read_text(encoding='utf-8'))
        executable=bundled_browser(project)
        assert executable and hashlib.sha256(Path(executable).read_bytes()).hexdigest()==manifest['executable_sha256']
        assert (Path(executable).parent/'ABOUT').is_file()
        with sync_playwright() as p:
            context=p.chromium.launch_persistent_context(str(target/'fixture-profile'),executable_path=executable,headless=True)
            page=context.pages[0]
            page.set_content('<title>Offline Chromium fixture</title><h1>Release verified</h1>')
            assert page.title()=='Offline Chromium fixture'
            assert page.locator('h1').inner_text()=='Release verified'
            browser_version=context.browser.version
            context.close()
        result={'zip_integrity':True,'no_private_state':True,'browser_sha256':True,
                'browser_started':True,'browser_version':browser_version,'playwright':manifest['playwright_version']}
    print(json.dumps(result))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('archive');args=parser.parse_args()
    verify(Path(args.archive))
