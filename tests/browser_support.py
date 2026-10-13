import os
import shutil
from pathlib import Path
from playwright.sync_api import sync_playwright


def browser_available():
    if os.environ.get('COURSE_CHROMIUM') or shutil.which('chromium') or shutil.which('google-chrome'):
        return True
    with sync_playwright() as p:
        return Path(p.chromium.executable_path).is_file()
