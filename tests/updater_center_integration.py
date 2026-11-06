"""Run only against the disposable /root/ctl-ci-platform Linux CI instance."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/team-deploy/assets/deployctl.pyz'))
from deployctl.updater import Host, private_directory
from deployctl.node_agent import read_json
from deployctl import server_bundle


def main():
    if sys.platform!='linux' or os.geteuid()!=0 or os.environ.get('CTL_DISPOSABLE_CENTER_FIXTURE')!='1':
        raise SystemExit('requires explicit disposable Linux root center fixture')
    home=Path('/root/ctl-ci-platform')
    executable='/root/ctl-ci-bin/ctl'
    instance=server_bundle.read_instance(home)
    if instance.get('origin')!='http://127.0.0.1:8091':
        raise SystemExit('unexpected fixture instance')
    source=ROOT/'dist/ctl-platform-v1.16.0.tar.gz'
    content=source.read_bytes();digest=hashlib.sha256(content).hexdigest()
    current=server_bundle.read_state(home/'server-state.json')['current']['version']
    config_path=Path('/var/lib/ctl-updater-center/updater.json')
    config_before=config_path.read_bytes()
    config=read_json(config_path)
    if (config['server']!=instance['origin'] or config['kind']!='center'
            or config['target_id']!='center' or config['cli_path']!=executable):
        raise RuntimeError('automatic center updater identity mismatch')
    subprocess.run([executable,'updater','setup','--kind','center','--server-home',str(home),
                    '--cli-path',executable],check=True,timeout=60)
    if config_path.read_bytes()!=config_before:raise RuntimeError('center repair rotated updater identity')
    subprocess.run(['systemctl','is-active','--quiet','ctl-updater-center.service'],check=True,timeout=30)
    before=hashlib.sha256((home/'keys/encryption.key').read_bytes()).hexdigest()
    host=Host({'kind':'center','target_id':'center','server':instance['origin'],
               'server_home':str(home),'cli_path':executable})
    with tempfile.TemporaryDirectory(dir='/root',prefix='ctl-center-update-fixture-') as temp:
        jobdir=private_directory(Path(temp))
        package=jobdir/source.name;package.write_bytes(content);package.chmod(0o600)
        Path(str(package)+'.sha256').write_text(digest+'\n')
        job={'kind':'center','from_version':current,'version':'v1.16.0','server_sha256':digest}
        host.backup(job,jobdir)
        dump=jobdir/'backup/database.dump'
        listing=subprocess.run(['docker','exec','-i','ctl-platform-database-1','pg_restore','--list'],
                               input=dump.read_bytes(),capture_output=True,timeout=60,check=True)
        if b'ctl_lifecycle_fixture' not in listing.stdout:
            raise RuntimeError('database backup did not include fixture data')
        host.install(job,jobdir,lambda stage:None)
        if config_path.read_bytes()!=config_before:raise RuntimeError('remote center update reconfigured its own identity')
        if not host.verify('v1.16.0',jobdir) or not host.reconcile(job,jobdir):
            raise RuntimeError('center update version/readiness not confirmed')
        if hashlib.sha256((home/'keys/encryption.key').read_bytes()).hexdigest()!=before:
            raise RuntimeError('center key changed')
        value=subprocess.run(['docker','exec','ctl-platform-database-1','psql','-U','ctl','-d','ctl','-Atc',
                              'SELECT value FROM ctl_lifecycle_fixture'],capture_output=True,text=True,check=True,timeout=30)
        if value.stdout.strip()!='42':raise RuntimeError('center fixture data changed')
        print('PASS: real updater database/config backup, center replacement, readiness reconciliation and retained data/keys')


if __name__=='__main__':main()
