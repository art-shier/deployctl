"""Bundle the CLI and pure-Python PyYAML, without a server-side pip install."""

import argparse
import hashlib
from pathlib import Path
import shutil
import tempfile
import zipapp

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
        zipapp.create_archive(stage, target, interpreter='/usr/bin/env python3',
                              compressed=True)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_name(target.name + '.sha256').write_text(f'{digest}  {target.name}\n', encoding='ascii')
    print(target)


if __name__ == '__main__':
    main()
