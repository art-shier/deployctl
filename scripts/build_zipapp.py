"""Bundle the CLI and pure-Python PyYAML, without a server-side pip install."""

import argparse
import hashlib
from pathlib import Path
import shutil
import tempfile
import zipfile

import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='dist/deployctl.pyz')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    target = Path(args.output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='deployctl-build-') as tmp:
        stage = Path(tmp)
        shutil.copytree(root / 'deployctl', stage / 'deployctl', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(Path(yaml.__file__).parent, stage / 'yaml',
                        ignore=shutil.ignore_patterns('__pycache__', '*.so', '*.pyd'))
        shutil.copyfile(root / 'THIRD_PARTY_NOTICES.txt', stage / 'THIRD_PARTY_NOTICES.txt')
        shutil.copyfile(root / 'LICENSE', stage / 'LICENSE')
        # zipapp's auto-generated entry point ignores main()'s return value.
        (stage / '__main__.py').write_text(
            'from deployctl.cli import main\nraise SystemExit(main())\n', encoding='utf-8')
        # Rebuilding the same release must preserve its digest across hosts.
        with target.open('wb') as output:
            output.write(b'#!/usr/bin/env python3\n')
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(stage.rglob('*'), key=lambda item: item.relative_to(stage).as_posix()):
                    if not path.is_file():
                        continue
                    name = path.relative_to(stage).as_posix()
                    raw = path.read_bytes()
                    if path.suffix in ('.py', '.txt') or path.name == 'LICENSE':
                        raw = raw.replace(b'\r\n', b'\n')
                    entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    entry.create_system = 3
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    entry.external_attr = 0o644 << 16
                    archive.writestr(entry, raw)
        target.chmod(0o755)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_name(target.name + '.sha256').write_text(f'{digest}  {target.name}\n', encoding='ascii', newline='\n')
    print(target)


if __name__ == '__main__':
    main()
