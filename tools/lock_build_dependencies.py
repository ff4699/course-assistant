"""Pin only declared runtime/build dependency trees, excluding old crawler extras."""
from importlib.metadata import distribution
from pathlib import Path
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
pending = ['fastapi', 'uvicorn', 'pydantic', 'playwright', 'httpx', 'pyinstaller', 'pillow']
packages = {}
while pending:
    name = canonicalize_name(pending.pop())
    if name in packages:
        continue
    package = distribution(name)
    packages[name] = package.version
    for text in package.requires or []:
        requirement = Requirement(text)
        if requirement.marker is None or requirement.marker.evaluate({'extra': ''}):
            pending.append(requirement.name)
(ROOT / 'requirements-build.lock').write_text(
    '# Windows x64 release build, Python 3.14. Exact versions used for v0.4.1.\n'
    + ''.join(f'{name}=={packages[name]}\n' for name in sorted(packages)), encoding='utf-8')
print(f'Locked {len(packages)} runtime/build dependencies')
