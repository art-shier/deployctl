"""Build CLI, installer and standalone skill assets for a GitHub release."""

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deployctl import __version__


def checksum(path):
    value = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + '.sha256').write_text(f'{value}  {path.name}\n', encoding='utf-8')


def main():
    dist = ROOT / 'dist'
    dist.mkdir(exist_ok=True)
    for script in ('build_install_script.py', 'build_zipapp.py'):
        subprocess.run([sys.executable, str(ROOT / 'scripts' / script)], cwd=ROOT, check=True)
    skill = ROOT / 'skills/team-deploy'
    for file in ('deployctl.pyz', 'deployctl.pyz.sha256'):
        shutil.copyfile(dist / file, skill / 'assets' / file)
    for file in ('release.yml', 'deploy.yml', 'deployment.yaml'):
        shutil.copyfile(ROOT / 'templates' / file, skill / 'assets/templates' / file)
    shutil.copyfile(ROOT / 'install.sh', skill / 'assets/install.sh')
    shutil.copyfile(ROOT / 'install.sh', dist / 'install.sh')
    checksum(dist / 'install.sh')
    archive = dist / f'team-deploy-skill-v{__version__}.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for path in sorted(skill.rglob('*')):
            relative = path.relative_to(skill)
            if not path.is_file() or '__pycache__' in relative.parts:
                continue
            item = zipfile.ZipInfo((Path('team-deploy') / relative).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o644 << 16
            package.writestr(item, path.read_bytes())
    checksum(archive)
    for wheel in dist.glob(f'team_deployctl-{__version__}-*.whl'):
        checksum(wheel)
    deliverables = [path for path in sorted(dist.iterdir())
                    if path.is_file() and path.suffix != '.sha256' and path.name != 'SHA256SUMS'
                    and (not path.name.endswith('.whl') or f'-{__version__}-' in path.name)]
    (dist / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n'
                                          for path in deliverables), encoding='utf-8')
    print(f'Built release assets v{__version__}')


if __name__ == '__main__':
    main()
