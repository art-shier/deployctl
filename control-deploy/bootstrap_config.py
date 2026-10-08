"""Create a private platform instance once; repeat runs preserve secrets and targets."""
import argparse
import json
import os
from pathlib import Path
import secrets
import stat
import sys
from urllib.parse import urlsplit


def plain(path):
    path=Path(path).absolute()
    for item in (path,*path.parents):
        if item.is_symlink(): raise ValueError('instance paths cannot contain links')
    return path


def secret_file(path):
    path=plain(path)
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size>16384 or (os.name!='nt' and (info.st_uid!=os.getuid() or info.st_mode&0o077)):
        raise ValueError('secret files require current ownership and private permissions')
    return path.read_text(encoding='utf-8').strip()


def write(path,raw):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as handle: handle.write(raw);handle.flush();os.fsync(handle.fileno())


def prepare(home, image, origin=None, registry_host=None, api_port=None, registry_port=None, database_url_file=None):
    home=plain(home)
    if any(c in str(home) for c in '\n\r$# \\'): raise ValueError('instance directory must be a plain absolute POSIX path')
    home.mkdir(parents=True,exist_ok=True,mode=0o700)
    info=home.stat()
    if os.name!='nt' and (info.st_uid!=os.getuid() or info.st_mode&0o077): raise ValueError('instance directory requires current ownership and permissions 700')
    path=home/'instance.json';plain(path)
    previous=json.loads(secret_file(path)) if path.exists() else None
    cfg=previous or {'origin':origin or 'https://ctl.shier.art','registry_host':registry_host or 'ctl.shier.art',
                     'api_port':api_port or 8080,'registry_port':registry_port or 5000,'external_database':bool(database_url_file),
                     'postgres_password':secrets.token_hex(24),'registry_secret':secrets.token_hex(32)}
    for key,value in [('origin',origin),('registry_host',registry_host),('api_port',api_port),('registry_port',registry_port)]:
        if previous and value is not None and value!=previous[key]: raise ValueError('existing instance settings differ; edit protected instance configuration deliberately')
    u=urlsplit(cfg['origin'])
    if not u.hostname or u.username or u.password or u.path or u.query or u.fragment or (u.scheme!='https' and not (u.scheme=='http' and u.hostname in ('127.0.0.1','localhost'))): raise ValueError('public origin must be HTTPS; loopback HTTP for tests only')
    if any(c in cfg['origin']+cfg['registry_host']+image for c in '\r\n $\\#'): raise ValueError('invalid Compose setting')
    r=urlsplit('https://'+cfg['registry_host'])
    if r.netloc!=cfg['registry_host'] or not r.hostname or r.username or r.password or r.path or r.query or r.fragment: raise ValueError('invalid registry host')
    for key in ('api_port','registry_port'):
        if type(cfg[key])!=int or not 1<=cfg[key]<=65535: raise ValueError('ports must be 1..65535')
    if cfg['api_port']==cfg['registry_port']: raise ValueError('API and Registry ports must differ')
    keys=plain(home/'keys');keys.mkdir(mode=0o700,exist_ok=True)
    for name in ('artifacts','registry'):
        plain(home/name).mkdir(mode=0o700,exist_ok=True)
    dsn=plain(keys/'database.url')
    if database_url_file:
        database=secret_file(database_url_file)
        parsed=urlsplit(database)
        if parsed.scheme not in ('postgres','postgresql') or not parsed.hostname or not parsed.path or any(ord(c)<33 for c in database): raise ValueError('external database requires a PostgreSQL connection URL')
        if previous and not cfg['external_database']: raise ValueError('cannot automatically switch the existing database')
        if dsn.exists() and dsn.read_text().strip()!=database: raise ValueError('cannot automatically change database target')
    elif cfg['external_database']:
        if not dsn.exists(): raise ValueError('external database URL file required')
        database=dsn.read_text().strip()
    else:
        database=f"postgres://ctl:{cfg['postgres_password']}@database:5432/ctl?sslmode=disable"
    if not dsn.exists(): write(dsn,database+'\n')
    if not previous: write(path,json.dumps(cfg,indent=2)+'\n')
    # Only non-user-generated fields and random hex secrets enter Compose interpolation.
    environment={'CTL_HOME':str(home),'CTL_IMAGE':image,'CTL_PUBLIC_ORIGIN':cfg['origin'],'CTL_REGISTRY_HOST':cfg['registry_host'],
                 'CTL_API_PORT':str(cfg['api_port']),'CTL_REGISTRY_PORT':str(cfg['registry_port']),
                 'CTL_POSTGRES_PASSWORD':cfg['postgres_password'],'CTL_REGISTRY_HTTP_SECRET':cfg['registry_secret']}
    env_path=plain(home/'compose.env')
    temporary=home/('.compose-'+secrets.token_hex(8));write(temporary,''.join(f'{k}={v}\n' for k,v in environment.items()));os.replace(temporary,env_path)
    return cfg


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--home',default='/opt/ctl-platform');parser.add_argument('--image',default='ctl-control:local')
    parser.add_argument('--origin');parser.add_argument('--registry-host');parser.add_argument('--api-port',type=int);parser.add_argument('--registry-port',type=int);parser.add_argument('--database-url-file')
    try:
        cfg=prepare(**vars(parser.parse_args()))
        print('external' if cfg['external_database'] else 'database')
    except (ValueError,OSError,KeyError):
        print('Invalid or conflicting platform configuration; check private instance files and parameters',file=sys.stderr);sys.exit(1)
