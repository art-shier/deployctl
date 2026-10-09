"""Build curated public Agent resources using only the Python standard library."""

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ASSETS = ('assets/deployctl.pyz', 'assets/deployctl.pyz.sha256',
          'assets/install.sh', 'assets/LICENSE', 'assets/THIRD_PARTY_NOTICES.txt',
          'assets/templates/release.yml', 'assets/templates/deploy.yml',
          'assets/templates/deployment.yaml')


def read_version(source):
    """Read a literal assignment without importing CLI dependencies or executing it."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == '__version__'
                                                for target in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                version = node.value.value
                if re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
                    return version
    raise ValueError('CLI version must be a literal semantic version')


def curated_files(skill_dir, include_agent=False):
    skill_dir = Path(skill_dir).absolute()
    names = ['SKILL.md', *ASSETS]
    names.extend(path.relative_to(skill_dir).as_posix()
                 for path in (skill_dir / 'references').glob('*.md'))
    if include_agent:
        names.append('agents/openai.yaml')
    files = []
    for name in sorted(set(names)):
        path = skill_dir / name
        for component in (path, *path.parents):
            if component.is_symlink():
                raise ValueError('public skill files cannot contain symlinks')
        if path.is_file():
            files.append((name, path))
        elif name == 'assets/deployctl.pyz.sha256':
            raise ValueError('bundled CLI checksum is missing')
        elif name in ('SKILL.md', 'assets/deployctl.pyz'):
            raise ValueError(f'required public skill file missing: {name}')
    paths = dict(files)
    digest = hashlib.sha256(paths['assets/deployctl.pyz'].read_bytes()).hexdigest()
    expected = f'{digest}  deployctl.pyz\n'.encode('ascii')
    if public_bytes('assets/deployctl.pyz.sha256', paths['assets/deployctl.pyz.sha256']) != expected:
        raise ValueError('bundled CLI checksum does not match the committed CLI')
    return files


def build_archive(skill_dir, output):
    """Write the shared release/Agent ZIP and return its path."""
    files = curated_files(skill_dir, include_agent=True)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in files:
            item = zipfile.ZipInfo('team-deploy/' + name, date_time=(1980, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.create_system = 3
            item.external_attr = 0o644 << 16
            archive.writestr(item, public_bytes(name, path))
    return output


def public_bytes(name, path):
    """Use identical cross-platform text bytes in public files and ZIP entries."""
    contents = path.read_bytes()
    return contents if name == 'assets/deployctl.pyz' else contents.replace(b'\r\n', b'\n')


def checksum(path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + '.sha256').write_text(f'{digest}  {path.name}\n', encoding='ascii', newline='\n')
    return digest


def build_bundle(root, output):
    root, output = Path(root), Path(output)
    version = read_version((root / 'deployctl/__init__.py').read_text(encoding='utf-8'))
    skill = root / 'skills/team-deploy'
    files = curated_files(skill)
    cli = skill / 'assets/deployctl.pyz'
    with zipfile.ZipFile(cli) as archive:
        bundled_version = read_version(archive.read('deployctl/__init__.py').decode('utf-8'))
    if bundled_version != version:
        raise ValueError(f'bundled CLI version {bundled_version} differs from source version {version}')
    result = subprocess.run([sys.executable, '-I', '-S', str(cli), '--version'],
                            check=True, capture_output=True, text=True)
    if result.stdout.strip() != version:
        raise ValueError('bundled CLI reported an unexpected version')
    output.mkdir(parents=True, exist_ok=True)
    for name, path in files:
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(public_bytes(name, path))
    archive = build_archive(skill, output / 'team-deploy-skill.zip')
    metadata = {'version': version, 'skill_sha256': checksum(archive),
                'cli_sha256': checksum(output / 'assets/deployctl.pyz')}
    (output / 'metadata.json').write_text(json.dumps(metadata, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    metadata = build_bundle(args.root, args.output)
    print(f"Built Agent resources v{metadata['version']}")


if __name__ == '__main__':
    main()
