"""Read-only integrity check of the original, extracted local handoff package."""
import hashlib
import json
from pathlib import Path, PurePosixPath


def main():
    root = Path(__file__).resolve().parents[1]
    manifest = root / 'HANDOFF_MANIFEST.json'
    if not manifest.is_file():
        print('HANDOFF_MANIFEST.json not found; use the complete original handoff ZIP.')
        return 1
    data = json.loads(manifest.read_text(encoding='utf-8'))
    failures = []
    for record in data['files']:
        relative = PurePosixPath(record['path'])
        if relative.is_absolute() or '..' in relative.parts or '.state' in relative.parts:
            failures.append('invalid manifest path: ' + str(relative))
            continue
        target = root.joinpath(*relative.parts)
        if not target.is_file():
            failures.append('missing: ' + str(relative))
            continue
        content = target.read_bytes()
        if len(content) != record['bytes'] or hashlib.sha256(content).hexdigest() != record['sha256']:
            failures.append('changed: ' + str(relative))
    if failures:
        print('\n'.join(failures))
        print('Changes after development are expected; do not delete private state or undo valid work.')
        return 1
    print(f"Verified {len(data['files'])} original handoff files. Private state was not read.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
