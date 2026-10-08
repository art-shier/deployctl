"""Prepare an external ctl database URL from ConfigHub without exposing secrets.

An explicit recovery option can retarget only an empty, failed first installation.
The published server archive, deployment state and other instance keys stay intact.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlsplit


def database_url(snapshot, project='shier', environment='prod'):
    if not isinstance(snapshot, dict) or snapshot.get('project') != project or snapshot.get('environment') != environment:
        raise ValueError('ConfigHub returned a different project or environment')
    values = snapshot.get('values')
    if not isinstance(values, dict): raise ValueError('Invalid ConfigHub response')
    host = values.get('ctl_db_address', values.get('db_address'))
    port = str(values.get('ctl_db_port', values.get('db_port', '')))
    user, password = values.get('ctl_db_username'), values.get('ctl_db_password')
    name, ssl = values.get('ctl_db_name', 'ctl'), values.get('ctl_db_sslmode', 'require')
    if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9:.\[\]-]+', host):
        raise ValueError('Invalid database host')
    if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError('Invalid database port')
    if not all(isinstance(value, str) and value and '\x00' not in value for value in (user, password)):
        raise ValueError('ConfigHub requires ctl_db_username and ctl_db_password; shared admin credentials are not used')
    if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', name) or name in ('postgres','template0','template1'):
        raise ValueError('Use a dedicated ctl database')
    if ssl not in ('require','verify-ca','verify-full'): raise ValueError('Database TLS is required')
    if ':' in host and not host.startswith('['): host = '[' + host + ']'
    url = f'postgresql://{quote(user,safe="")}:{quote(password,safe="")}@{host}:{int(port)}/{name}?sslmode={ssl}&connect_timeout=10'
    parsed = urlsplit(url)
    if not parsed.hostname: raise ValueError('Invalid database host')
    return url


def fetch_config(project, environment, server, token_file=None):
    argv = ['confighub','--server',server]
    if token_file:
        restricted(Path(token_file))
        argv += ['--token-file',str(token_file)]
    argv += ['export','--project',project,'--env',environment,'--format','json']
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('ConfigHub CLI unavailable or request timed out') from exc
    if result.returncode or len(result.stdout.encode('utf-8')) > 2 * 1024 * 1024:
        raise ValueError('ConfigHub read failed; check server, Token and project/environment read scope')
    try: return json.loads(result.stdout)
    except ValueError as exc: raise ValueError('Invalid ConfigHub JSON response') from exc


def trusted(path, key=False):
    path = Path(path).absolute()
    if '..' in path.parts: raise ValueError('Parent traversal is not allowed')
    for item in (path,*path.parents):
        if item.is_symlink(): raise ValueError('Database preparation paths cannot contain links')
    for parent in path.parents:
        if not parent.exists(): continue
        info=parent.stat()
        sticky_root=info.st_uid==0 and info.st_mode & stat.S_ISVTX
        key_parent=key and parent==path.parent and parent.name=='keys' and info.st_uid==10001
        if not stat.S_ISDIR(info.st_mode) or (info.st_uid not in (0,os.geteuid()) and not key_parent) or (info.st_mode & 0o022 and not sticky_root):
            raise ValueError('Database preparation requires trusted parent directories')
    return path


def restricted(path, key=False):
    path=trusted(path,key);info=path.stat()
    owners=(os.geteuid(),10001) if key else (os.geteuid(),)
    if not stat.S_ISREG(info.st_mode) or info.st_uid not in owners or info.st_mode & 0o077 or info.st_size>65536:
        raise ValueError('Configuration files require private permissions and expected ownership')
    return path


def atomic(path, raw, key=False):
    path=trusted(path,key)
    info=restricted(path,key).stat() if path.exists() else None
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd, temporary=tempfile.mkstemp(prefix='.ctl-db-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        if info and os.geteuid()==0: os.chown(temporary,info.st_uid,info.st_gid)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def prepare(snapshot, output, home, repair=False, project='shier', environment='prod'):
    url=database_url(snapshot,project,environment)
    output,home=trusted(output),trusted(home)
    if output==home or home in output.parents:
        raise ValueError('Connection output must be outside the platform instance directory')
    if output.exists(): restricted(output)
    path=trusted(home/'instance.json')
    cfg=None
    if path.exists():
        cfg=json.loads(restricted(path).read_text(encoding='utf-8'))
        dsn=trusted(home/'keys/database.url',key=True)
        if cfg.get('external_database') and restricted(dsn,True).read_text().strip()==url:
            atomic(output,url+'\n');return
        if not repair: raise ValueError('Existing database differs; recovery requires --repair-initial-install')
        state=json.loads(restricted(home/'server-state.json').read_text(encoding='utf-8'))
        if state.get('schema_version')!=1 or state.get('current') is not None or not state.get('pending'):
            raise ValueError('Only a failed first installation can change database; an installed server needs a data migration')
        local=trusted(home/'database')
        if local.exists() and (not local.is_dir() or any(local.iterdir())):
            raise ValueError('Local database contains data; migrate it before changing database')
        previous=restricted(dsn,True).read_text().strip()
        parsed=urlsplit(previous)
        # Permit recovery after a crash between the URL and instance writes.
        if previous!=url and (cfg.get('external_database') or parsed.hostname!='database' or parsed.path!='/ctl'):
            raise ValueError('Existing external database target cannot be changed automatically')
        try:
            process=subprocess.run(['docker','ps','-aq','--filter','label=com.docker.compose.project=ctl-platform'],capture_output=True,text=True,timeout=30)
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise ValueError('Cannot verify platform containers; no configuration was changed') from exc
        if process.returncode or process.stdout.strip():
            raise ValueError('Existing platform containers require inspection before changing database')
    atomic(output,url+'\n')
    if cfg is not None:
        atomic(home/'keys/database.url',url+'\n',key=True)
        cfg['external_database']=True
        atomic(path,json.dumps(cfg,indent=2)+'\n')


@contextmanager
def lock(path):
    import fcntl
    path=trusted(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    info=path.parent.stat()
    if info.st_uid!=os.geteuid() or info.st_mode & 0o077:
        raise ValueError('Lock directory must be private and current-owned')
    descriptor=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        restricted(path)
        try: fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc: raise ValueError('Another ctl platform operation is running') from exc
        yield
    finally: os.close(descriptor)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',default='shier');parser.add_argument('--env',default='prod')
    parser.add_argument('--server',default='https://config.shier.art');parser.add_argument('--token-file')
    parser.add_argument('--output',default='/etc/deployctl/ctl-database.url')
    parser.add_argument('--home',default='/opt/ctl-platform')
    parser.add_argument('--repair-initial-install',action='store_true')
    args=parser.parse_args()
    if sys.platform!='linux' or os.geteuid()!=0: raise ValueError('Requires Linux root and ConfigHub CLI')
    home=trusted(args.home);home.mkdir(parents=True,exist_ok=True,mode=0o700)
    info=home.stat()
    if info.st_uid!=0 or info.st_mode & 0o077: raise ValueError('Instance directory must be root-owned and private')
    with lock(Path('/run/ctl-platform-cli/operation.lock')),lock(home/'.server.lock'):
        snapshot=fetch_config(args.project,args.env,args.server,args.token_file)
        prepare(snapshot,args.output,home,args.repair_initial_install,args.project,args.env)
    print('External database configuration prepared. Retry the original Release with --database-url-file '+str(args.output))


if __name__=='__main__':
    try: main()
    except (ValueError,OSError,KeyError,TypeError):
        # JSON/URL/process diagnostics can contain credentials: do not print raw errors.
        print('Database preparation failed. Check private paths, ConfigHub read scope and ctl_db_* settings. Recovery requires an empty failed first install with no platform containers; installed databases require migration.',file=sys.stderr)
        sys.exit(1)
