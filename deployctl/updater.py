"""Independent protocol-1 host updater. Remote jobs select releases, never commands."""
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from urllib.request import Request

from . import __version__, bootstrap, server_bundle
from .node_agent import read_json, write_json, read_private, private_directory
from .platform_client import PlatformClient, validate_origin, validate_token
from .platform_credentials import Credentials
from .runtime_snapshot import reject_links, validate_id

DEFAULT_HOME = '/var/lib/ctl-updater'
TERMINAL = {'succeeded', 'rolled_back', 'failed', 'needs_attention', 'cancelled'}


def fsync_directory(path):
    if os.name=='nt': return
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def release_version(value):
    if not isinstance(value, str) or len(value)>32 or not re.fullmatch(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value):
        raise ValueError('an exact stable release is required')
    parts = tuple(map(int, value[1:].split('.')))
    if not (1, 15, 0) <= parts < (2, 0, 0): raise ValueError('release is outside updater protocol 1')
    return parts


def installed_version(value):
    if not isinstance(value, str) or not re.fullmatch(r'v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value):
        raise ValueError('invalid installed version')
    return tuple(map(int, value.lstrip('v').split('.')))


def validate_job(job, identity):
    if not isinstance(job, dict): raise ValueError('invalid updater job')
    allowed={'id','request_id','batch_id','updater_id','kind','target_id','version','from_version',
             'cli_sha256','server_sha256','status','stage','result_code','current_version',
             'created_at','updated_at','parallelism','resolved_at'}
    if set(job)-allowed or len(json.dumps(job))>65536: raise ValueError('unsupported updater job fields')
    validate_id(job.get('id'))
    for field in ('updater_id', 'kind', 'target_id'):
        if job.get(field) != identity.get(field): raise ValueError('updater job identity mismatch')
    if release_version(job.get('version')) <= installed_version(job.get('from_version')):
        raise ValueError('updater requires a strictly newer release')
    for field in ('cli_sha256', 'server_sha256'):
        if not isinstance(job.get(field), str) or not re.fullmatch('[a-f0-9]{64}', job[field]):
            raise ValueError('updater job requires pinned SHA256')
    if job.get('status') not in ('leased', 'running'): raise ValueError('job has no execution lease')
    return job


class Updater:
    """Inject client.json and a Host backend for protocol/lifecycle fixtures."""
    def __init__(self, home, identity, client, host):
        self.home = private_directory(home)
        self.identity, self.client, self.host = identity, client, host
        self.jobs = private_directory(self.home / 'jobs')
        fsync_directory(self.home)

    def save(self, directory, record):
        write_json(directory / 'journal.json', record)

    def report(self, directory, record):
        try:
            self.client.json('POST', '/api/v1/updater/jobs/' + record['job']['id'] + '/report', record['report'])
        except (RuntimeError, OSError):
            return False
        record['acknowledged'] = True
        self.save(directory, record)
        return True

    def finish(self, directory, record, status, code):
        try: current = self.host.readiness()[0]
        except (ValueError, RuntimeError, OSError): current = ''
        record['report'] = dict(status=status, stage='completed' if status in ('succeeded','rolled_back') else 'interrupted' if status=='needs_attention' else 'failed', result_code=code, current_version=current)
        self.save(directory, record)
        self.report(directory, record)

    def recover(self):
        blocked = False
        for directory in sorted(self.jobs.iterdir()):
            reject_links(directory)
            # These are pre-commit creations: no server start was ever attempted.
            if directory.name.startswith('.stage-'): continue
            if not directory.is_dir(): raise ValueError('invalid updater journal directory')
            record = read_json(directory / 'journal.json')
            validate_job(record['job'], self.identity)
            if directory.name != record['job']['id']: raise ValueError('journal identity mismatch')
            if 'report' not in record:
                success = False
                if record['job']['kind'] == 'center' and record.get('stage') in ('installing','restarting','verifying'):
                    try: success = self.host.reconcile(record['job'], directory)
                    except (ValueError, RuntimeError, OSError): pass
                self.finish(directory, record, 'succeeded' if success else 'needs_attention', 'ok' if success else 'interrupted')
            elif not record.get('acknowledged'):
                self.report(directory, record)
            blocked |= not record.get('acknowledged')
        return blocked

    def run_once(self):
        # Reconcile/report before heartbeat: center may have disappeared mid-update.
        if self.recover(): return
        current, ready, reason = self.host.readiness()
        self.client.json('POST', '/api/v1/updater/heartbeat', dict(current_version=current, helper_version='v'+__version__, protocol=1, platform='linux', ready=ready, blocked_reason=reason))
        if not ready: return
        response = self.client.json('POST', '/api/v1/updater/poll', {})
        job = response.get('job')
        if not job: return
        validate_job(job, self.identity)
        directory = self.jobs / job['id']
        if directory.exists():
            record = read_json(directory / 'journal.json')
            if record.get('report'): self.report(directory, record)
            return
        record = dict(job=job, stage='starting', acknowledged=False)
        staging=Path(tempfile.mkdtemp(prefix='.stage-',dir=self.jobs))
        self.save(staging,record)
        os.rename(staging,directory)
        fsync_directory(self.jobs)
        self.save(directory, record)  # durable intent precedes ambiguous network start
        if job['status'] == 'running':
            self.finish(directory, record, 'needs_attention', 'interrupted')
            return
        try:
            started = self.client.json('POST', '/api/v1/updater/jobs/' + job['id'] + '/start', {})
            if started.get('status') != 'running': raise ValueError('start was not acknowledged')
            validate_job(started, self.identity)
            if any(started.get(k) != job.get(k) for k in ('id','version','from_version','cli_sha256','server_sha256')):
                raise ValueError('start changed the frozen job')
        except (ValueError, RuntimeError, OSError):
            self.finish(directory, record, 'needs_attention', 'interrupted'); return
        def stage(value):
            record['stage'] = value; self.save(directory, record)
            try:
                self.client.json('POST', '/api/v1/updater/jobs/' + job['id'] + '/report', dict(status='running',stage=value,result_code='',current_version=self.host.readiness()[0]))
            except (ValueError, RuntimeError, OSError): pass
        try:
            if current != job['from_version']: raise ValueError('installed version changed since scheduling')
            stage('downloading'); self.host.prepare(job, directory)
            stage('backing_up'); self.host.backup(job, directory)
            stage('installing'); self.host.install(job, directory, stage)
            stage('verifying')
            if not self.host.verify(job['version'], directory): raise RuntimeError('health verification failed')
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError):
            failed_stage = record['stage']
            if failed_stage in ('starting','downloading','backing_up'):
                self.finish(directory, record, 'failed', 'backup_failed' if failed_stage=='backing_up' else 'download_failed'); return
            if job['kind'] == 'node':
                try:
                    stage('restoring'); self.host.restore(job, directory, stage)
                    if not self.host.verify(job['from_version'], directory): raise RuntimeError('recovery failed')
                    self.finish(directory, record, 'rolled_back', 'rolled_back'); return
                except (ValueError, RuntimeError, OSError, subprocess.SubprocessError): pass
            self.finish(directory, record, 'needs_attention', 'manual_recovery_required'); return
        self.finish(directory, record, 'succeeded', 'ok')


def require_root():
    if sys.platform != 'linux' or os.geteuid() != 0: raise ValueError('updater setup and operation require Linux root')


def trusted(path, private=False, owners=(0,)):
    path = Path(path).absolute()
    if '..' in path.parts or any(ord(c)<32 for c in str(path)): raise ValueError('unsafe updater path')
    reject_links(path)
    for parent in path.parents:
        if parent.exists():
            info=parent.stat()
            if os.name!='nt' and (info.st_uid != 0 or info.st_mode & 0o022):
                raise ValueError('updater path parents must be root-owned and non-writable')
    if path.exists():
        info=path.stat()
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)): raise ValueError('invalid updater path type')
        if os.name!='nt' and (info.st_uid not in owners or info.st_mode & (0o077 if private else 0o022)):
            raise ValueError('unsafe updater ownership or permissions')
    return path


def installer_identity(executable):
    executable=trusted(executable)
    marker=trusted(executable.parent/'.deployctl-install.json', private=True)
    metadata=read_json(marker)
    if metadata.get('schema_version')!=1 or metadata.get('repository')!=bootstrap.DEFAULT_REPOSITORY:
        raise ValueError('only official installer-managed executables can be updated')
    files=metadata.get('files'); primary=metadata.get('primary_name'); commands=metadata.get('commands')
    if (not isinstance(files,dict) or not isinstance(primary,str) or not re.fullmatch('[A-Za-z0-9_.-]+',primary)
        or primary in ('.','..') or not isinstance(commands,list) or set(commands) not in ({primary},{primary,'ctl'})
        or len(commands)!=len(set(commands)) or set(files)!=set(commands) or executable.name not in commands):
        raise ValueError('invalid installer identity')
    if Path(metadata.get('directory','')).absolute()!=executable.parent: raise ValueError('installer directory mismatch')
    for name,digest in files.items():
        path=trusted(executable.parent/name)
        if not path.is_file() or path.stat().st_size>bootstrap.MAX_DOWNLOAD or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('managed executable has changed')
    installed_version(metadata.get('version'))
    actual='v'+bootstrap.artifact_version(executable)
    if actual!=metadata['version']: raise ValueError('installed executable version mismatch')
    return metadata


def read_platform_key(path):
    """Platform keys may belong to container uid 10001; never follow a leaf link."""
    reject_links(path)
    flags=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0)
    with os.fdopen(os.open(path,flags),'rb') as stream:
        info=os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size>1024*1024
            or os.name!='nt' and (info.st_uid not in (0,10001) or info.st_mode & 0o022)):
            raise ValueError('unsafe center key file')
        raw=stream.read(1024*1024+1)
        if len(raw)>1024*1024: raise ValueError('center key exceeds size limit')
        return raw


def pg_dump_executable():
    """Validate root-controlled system links; retain pg_dump argv[0] for wrappers."""
    executable=shutil.which('pg_dump',path='/usr/bin:/usr/local/bin:/bin')
    if not executable: raise ValueError('external database backup requires pg_dump')
    original=Path(executable)
    if not original.is_absolute() or any(ord(c)<32 for c in str(original)):
        raise ValueError('unsafe PostgreSQL client path')
    current=Path(original.anchor); pending=list(original.parts[1:]); links=0
    root_info=current.lstat()
    if root_info.st_uid!=0 or root_info.st_mode & 0o022 or not stat.S_ISDIR(root_info.st_mode):
        raise ValueError('unsafe PostgreSQL client root')
    while pending:
        part=pending.pop(0)
        if part=='.': continue
        if part=='..': current=current.parent; continue
        candidate=current/part; info=candidate.lstat()
        if info.st_uid!=0: raise ValueError('PostgreSQL client paths must be root-owned')
        if stat.S_ISLNK(info.st_mode):
            links+=1
            if links>40: raise ValueError('PostgreSQL client symlink chain exceeds limit')
            target=Path(os.readlink(candidate))
            if target.is_absolute():
                current=Path(target.anchor)
                pending=list(target.parts[1:])+pending
            else:
                pending=list(target.parts)+pending
            continue
        if info.st_mode & 0o022: raise ValueError('PostgreSQL client paths must not be writable by other users')
        if pending and not stat.S_ISDIR(info.st_mode): raise ValueError('invalid PostgreSQL client ancestor')
        current=candidate
    info=current.lstat()
    if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
        raise ValueError('PostgreSQL client must be a regular executable')
    return str(original)


class Host:
    def __init__(self, config, runner=subprocess.run):
        self.config, self.runner = config, runner
        self.executable = Path(config['cli_path'])
        self.kind = config['kind']
        self.unit = config.get('agent_service','ctl-agent.service')
        if not re.fullmatch(r'[A-Za-z0-9_-]+\.service',self.unit) or self.unit=='ctl-updater.service':
            raise ValueError('invalid Agent unit')
        self.agent_home = Path(config.get('agent_home','/var/lib/ctl-agent'))
        self.server_home = Path(config.get('server_home','/opt/ctl-platform'))

    def command(self, argv, timeout=30, env=None, stdout=None):
        # argv are constructed locally only; no task may provide command arguments.
        environment={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'}
        if env: environment.update(env)
        result=self.runner(argv,stdout=stdout if stdout is not None else subprocess.PIPE,
                           stderr=subprocess.PIPE,env=environment,timeout=timeout)
        if result.returncode: raise RuntimeError('local updater operation failed')
        return result.stdout.decode() if isinstance(result.stdout,bytes) else (result.stdout or '')

    def service_info(self):
        raw=self.command(['systemctl','show',self.unit,'--property=FragmentPath,User,ExecStart,ActiveState,MainPID,KillSignal,KillMode'])
        info=dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
        fragment=trusted(info.get('FragmentPath',''))
        if not fragment.is_file() or info.get('User') not in ('','root'):
            raise ValueError('Agent unit is not a trusted root service')
        import shlex
        match=re.search(r'argv\[\]=(.*?) ;',info.get('ExecStart',''))
        if not match: raise ValueError('unrecognized Agent service command')
        args=shlex.split(match.group(1))
        if args[:3]!=[str(self.executable),'agent','run'] or len(args[3:])%2:
            raise ValueError('Agent service executable mismatch')
        options={}
        for i in range(3,len(args),2):
            if args[i] not in ('--home','--root','--config-root') or args[i] in options:
                raise ValueError('unexpected Agent service arguments')
            options[args[i]]=args[i+1]
        if options.get('--home','/var/lib/ctl-agent')!=str(self.agent_home):
            raise ValueError('Agent service home mismatch')
        if info.get('KillSignal') not in ('15','SIGTERM') or info.get('KillMode') not in ('mixed','control-group'):
            raise ValueError('Agent unit must stop gracefully')
        return info

    def center_state(self):
        trusted(self.server_home,private=True)
        self.center_identity()
        return server_bundle.read_state(self.server_home/'server-state.json')

    def center_identity(self):
        instance=server_bundle.read_instance(self.server_home)
        if validate_origin(instance.get('origin'))!=validate_origin(self.config.get('server')):
            raise ValueError('managed center origin does not match updater identity')

    def readiness(self):
        current=''
        try:
            if self.kind=='node':
                current=installer_identity(self.executable)['version']
                trusted(self.agent_home,private=True)
                identity=read_json(self.agent_home/'node.json')
                if identity.get('node_id')!=self.config['target_id'] or identity.get('server')!=self.config['server']:
                    return current,False,'setup_required'
                info=self.service_info()
                if info.get('ActiveState')!='active' or int(info.get('MainPID','0'))<=0:
                    return current,False,'agent_unavailable'
                release_version(current)
                marker=read_json(self.agent_home/'readiness.json')
                age=time.time_ns()-marker.get('heartbeat_ns',0)
                if (marker.get('version')!=current or marker.get('node_id')!=self.config['target_id']
                    or marker.get('pid')!=int(info['MainPID']) or marker.get('enabled') is not True
                    or not 0<=age<90*1_000_000_000):
                    return current,False,'agent_unavailable'
            else:
                state=self.center_state(); current=(state['current'] or {}).get('version','')
                if state['pending']: return current,False,'pending_operation'
                if not current: return '',False,'setup_required'
                cfg=server_bundle.read_instance(self.server_home)
                if cfg['external_database']:
                    try: pg_dump_executable()
                    except (ValueError,OSError): return current,False,'backup_unavailable'
                server_bundle.wait_ready(cfg['api_port'],timeout=5)
            return current,True,''
        except (ValueError,RuntimeError,OSError,subprocess.SubprocessError):
            return current,False,'service_unavailable'

    def prepare(self,job,directory):
        tag=job['version']; release_version(tag)
        # Independently reject a draft/prerelease even if catalog pins its digest.
        raw=bootstrap.fetch(Request('https://api.github.com/repos/art-shier/deployctl/releases/tags/'+tag,
            headers={'Accept':'application/vnd.github+json','User-Agent':'ctl-updater'}),1024*1024)
        release=json.loads(raw)
        if release.get('tag_name')!=tag or release.get('draft') is not False or release.get('prerelease') is not False:
            raise ValueError('release is not an official stable publication')
        name='deployctl.pyz' if self.kind=='node' else 'ctl-platform-'+tag+'.tar.gz'
        limit=bootstrap.MAX_DOWNLOAD if self.kind=='node' else server_bundle.MAX_EXPANDED
        content=bootstrap.fetch(Request('https://github.com/art-shier/deployctl/releases/download/'+tag+'/'+name),limit)
        digest=job['cli_sha256' if self.kind=='node' else 'server_sha256']
        if hashlib.sha256(content).hexdigest()!=digest: raise ValueError('release checksum mismatch')
        path=directory/name; bootstrap.atomic_write(path,content,0o600)
        bootstrap.atomic_write(Path(str(path)+'.sha256'),(digest+'\n').encode(),0o600)
        if self.kind=='node':
            if 'v'+bootstrap.artifact_version(path)!=tag: raise ValueError('release embedded version mismatch')
        elif server_bundle.read_bundle(path)[0]['version']!=tag: raise ValueError('server bundle version mismatch')

    def backup(self,job,directory):
        backup=private_directory(directory/'backup')
        if self.kind=='node':
            metadata=installer_identity(self.executable)
            if metadata['version']!=job['from_version']: raise ValueError('installed version changed')
            self.service_info()
            manifest={}
            for name in [*metadata['commands'],'.deployctl-install.json']:
                path=trusted(self.executable.parent/name)
                raw=path.read_bytes(); bootstrap.atomic_write(backup/name,raw,0o600)
                manifest[name]={'sha256':hashlib.sha256(raw).hexdigest(),'mode':stat.S_IMODE(path.stat().st_mode)}
            write_json(backup/'snapshot.json',manifest)
        else:
            state=self.center_state()
            if state['pending'] or not state['current'] or state['current']['version']!=job['from_version']:
                raise ValueError('center has changed or has a pending operation')
            server_bundle.check_project(self.server_home)
            for name in ('instance.json','compose.env','server-state.json'):
                path=trusted(self.server_home/name,private=True)
                bootstrap.atomic_write(backup/name,path.read_bytes(),0o600)
            keys=self.server_home/'keys'; reject_links(keys)
            info=keys.stat()
            if os.name!='nt' and (info.st_uid not in (0,10001) or info.st_mode & 0o022):
                raise ValueError('unsafe center keys directory')
            destination=private_directory(backup/'keys')
            for path in keys.iterdir():
                bootstrap.atomic_write(destination/path.name,read_platform_key(path),0o600)
            fsync_directory(destination)
            cfg=server_bundle.read_instance(self.server_home)
            env=None
            if cfg['external_database']:
                executable=pg_dump_executable()
                argv=[executable,'--format=custom','--no-owner','--no-acl']
                env={'PGDATABASE':(destination/'database.url').read_text().strip()}
            else:
                active=state['current']; bundle=self.server_home/'server-releases'/active['sha256']
                server_bundle.installed_content(bundle,active)
                argv=['docker','compose','--project-name','ctl-platform','--env-file',str(self.server_home/'compose.env'),
                    '-f',str(bundle/'control-deploy/compose.yaml'),'--profile','database','exec','-T','database',
                    'pg_dump','-U','ctl','-d','ctl','--format=custom','--no-owner','--no-acl']
            path=backup/'database.dump'; reject_links(path)
            with path.open('xb') as stream:
                os.chmod(path,0o600); self.command(argv,timeout=900,env=env,stdout=stream); stream.flush(); os.fsync(stream.fileno())
            with path.open('rb') as stream:
                if stream.read(5)!=b'PGDMP': raise ValueError('database backup is not a valid dump')
            write_json(backup/'complete.json',{'version':job['from_version'],'database_bytes':path.stat().st_size})

    def stop_agent(self):
        self.service_info()
        self.command(['systemctl','stop',self.unit],timeout=930)
        info=self.service_info()
        if info.get('ActiveState') not in ('inactive','failed') or info.get('MainPID')!='0': raise RuntimeError('Agent has not fully stopped')
        # Agent holds this lock for its lifetime; acquire only after systemd stop.
        from .runtime import service_lock
        trusted(self.agent_home/'.lock')
        with service_lock(self.agent_home/'.lock'): pass

    def restart_agent(self,directory):
        info=self.service_info()
        if info.get('ActiveState') not in ('inactive','failed') or info.get('MainPID')!='0':
            raise RuntimeError('Agent has not fully stopped before restart')
        write_json(directory/'restart.json',{'after_ns':time.time_ns()})
        # Normal inactive units may have been unloaded by systemd GC; resetting
        # them fails with "unit not loaded". Failed units retain their state and
        # must have restart limits cleared successfully before the next start.
        if info['ActiveState']=='failed':
            self.command(['systemctl','reset-failed',self.unit],timeout=30)
        self.command(['systemctl','start',self.unit],timeout=60)

    def install(self,job,directory,stage):
        if self.kind=='node':
            metadata=installer_identity(self.executable)
            stage('stopping'); self.stop_agent()
            # Revalidate all managed identities after stopping before replacement.
            installer_identity(self.executable)
            stage('installing')
            bootstrap.install(artifact=directory/'deployctl.pyz',install_dir=self.executable.parent,
                repository=bootstrap.DEFAULT_REPOSITORY,version=job['version'],expected_sha256=job['cli_sha256'],
                primary_name=metadata['primary_name'],no_alias='ctl' not in metadata['commands'])
            stage('restarting'); self.restart_agent(directory)
        else:
            state=self.center_state()
            if state['pending'] or state['current']['version']!=job['from_version']: raise ValueError('center operation changed')
            stage('installing')
            server_bundle.deploy_server(source=directory/('ctl-platform-'+job['version']+'.tar.gz'),
                home=self.server_home,expected_sha256=job['server_sha256'],upgrade=True,
                expected_previous=read_json(directory/'backup'/'server-state.json'),
                preflight=lambda:self.check_center_snapshot(directory))

    def check_center_snapshot(self,directory):
        self.center_identity()
        backup=directory/'backup'
        read_json(backup/'complete.json')
        for name in ('instance.json','compose.env','server-state.json'):
            current=trusted(self.server_home/name,private=True)
            if current.read_bytes()!=read_private(backup/name): raise ValueError('center configuration changed after backup')
        keys=self.server_home/'keys'; reject_links(keys)
        if {p.name for p in keys.iterdir()}!={p.name for p in (backup/'keys').iterdir()}:
            raise ValueError('center keys changed after backup')
        for saved in (backup/'keys').iterdir():
            current=keys/saved.name; reject_links(current)
            if read_platform_key(current)!=read_private(saved): raise ValueError('center keys changed after backup')

    def verify(self,version,directory):
        deadline=time.monotonic()+120
        while True:
            try:
                if self.kind=='center':
                    state=self.center_state()
                    if state['pending'] or not state['current'] or state['current']['version']!=version: return False
                    server_bundle.wait_ready(server_bundle.read_instance(self.server_home)['api_port'],timeout=5)
                    return True
                info=self.service_info(); marker=read_json(self.agent_home/'readiness.json')
                after=read_json(directory/'restart.json')['after_ns']
                if (info['ActiveState']=='active' and installer_identity(self.executable)['version']==version
                    and marker.get('version')==version and marker.get('node_id')==self.config['target_id']
                    and type(marker.get('heartbeat_ns')) is int and marker['heartbeat_ns']>after
                    and marker.get('pid')==int(info['MainPID']) and marker.get('enabled') is True):
                    return True
            except (ValueError,RuntimeError,OSError,subprocess.SubprocessError): pass
            if time.monotonic()>=deadline: return False
            time.sleep(2)

    def restore(self,job,directory,stage):
        self.stop_agent()
        backup=directory/'backup'; manifest=read_json(backup/'snapshot.json')
        original=read_json(backup/'.deployctl-install.json')
        if set(manifest)!={*original['commands'],'.deployctl-install.json'}: raise ValueError('invalid rollback snapshot')
        # Validate all backup bytes before touching any live file.
        payload=[]
        for name,record in manifest.items():
            if not re.fullmatch('[A-Za-z0-9_.-]+',name) or name in ('.','..'): raise ValueError('invalid backup filename')
            target=trusted(self.executable.parent/name); source=trusted(backup/name,private=True)
            raw=source.read_bytes()
            if (hashlib.sha256(raw).hexdigest()!=record['sha256'] or type(record['mode']) is not int
                or not 0<=record['mode']<=0o777 or os.name!='nt' and record['mode'] & 0o022):
                raise ValueError('rollback snapshot modified')
            payload.append((target,raw,record['mode']))
        for target,raw,mode in payload: bootstrap.atomic_write(target,raw,mode)
        installer_identity(self.executable)
        self.restart_agent(directory)

    def reconcile(self,job,directory):
        state=self.center_state()
        return (not state['pending'] and state['current'] and state['current']['version']==job['version']
                and state['current']['sha256']==job['server_sha256'] and self.verify(job['version'],directory))


def service_unit(home):
    home=Path(home)
    def quote(value):
        text=str(value)
        if not text.startswith('/') or any(ord(c)<32 for c in text): raise ValueError('systemd requires absolute Linux paths')
        return '"'+text.replace('\\','\\\\').replace('"','\\"').replace('%','%%').replace('$','$$')+'"'
    config=read_json(home/'updater.json')
    return ('[Unit]\nDescription=ctl independent system updater\nWants=network-online.target\nAfter=network-online.target\n\n'
        '[Service]\nType=simple\nUser=root\nUMask=0077\nExecStart='+quote(config['python'])+' -I -S '+quote(home/'bootstrap.pyz')+
        ' updater run --home '+quote(home)+'\nRestart=always\nRestartSec=10\nTimeoutStopSec=2000\nKillMode=mixed\n'
        '\n[Install]\nWantedBy=multi-user.target\n')


def enroll(args,token, transport=None):
    require_root()
    from .runtime import service_lock
    import zipfile
    home=trusted(args.home,private=True); home=private_directory(home)
    trusted(home/'.lock')
    with service_lock(home/'.lock'):
        jobs=home/'jobs'
        if jobs.exists():
            for path in jobs.iterdir():
                reject_links(path)
                if path.name.startswith('.stage-'): continue
                record=read_json(path/'journal.json')
                if not record.get('acknowledged') or record.get('report',{}).get('status') not in TERMINAL:
                    raise ValueError('unfinished updater journal prevents identity replacement')
        config=dict(kind=args.kind,target_id=args.target_id,cli_path=str(trusted(args.cli_path)),
                    agent_home=str(trusted(args.agent_home,private=True)),agent_service=args.agent_service,
                    server_home=str(trusted(args.server_home,private=True)),server=validate_origin(args.server),
                    python=str(trusted(Path(sys.executable).resolve())))
        existing=home/'updater.json'
        if existing.exists():
            previous=read_json(existing)
            if (previous.get('kind')!=config['kind'] or previous.get('target_id')!=config['target_id']
                or validate_origin(previous.get('server'))!=config['server']):
                raise ValueError('existing updater identity belongs to another target or server')
        if args.kind=='node': validate_id(args.target_id)
        elif args.target_id!='center': raise ValueError('center target must be center')
        metadata=installer_identity(config['cli_path'])
        release_version(metadata['version'])
        executable=Path(config['cli_path'])
        if not zipfile.is_zipfile(executable): raise ValueError('updater bootstrap must be an installed zipapp')
        host=Host(config)
        if args.kind=='node':
            identity=read_json(Path(config['agent_home'])/'node.json')
            if identity.get('node_id')!=args.target_id or identity.get('server')!=config['server']:
                raise ValueError('Agent identity does not match updater target')
            host.service_info()
        else:
            state=host.center_state()
            if state['pending'] or not state['current']: raise ValueError('managed center must have no pending operation')
        # Stage bytes in memory; a refused rotation must preserve the old helper.
        bootstrap_content=executable.read_bytes()
        client=PlatformClient(Credentials(transport or config['server'],validate_token(token)))
        response=client.json('POST','/api/v1/updater/enroll',dict(helper_version='v'+__version__,platform='linux',protocol=1))
        validate_id(response.get('updater_id')); validate_token(response.get('token'))
        if response.get('kind')!=args.kind or response.get('target_id')!=args.target_id:
            raise ValueError('enrollment target mismatch')
        config.update(updater_id=response['updater_id'],token=response['token'])
        bootstrap.atomic_write(home/'bootstrap.pyz',bootstrap_content,0o600)
        write_json(home/'updater.json',config)
        return response['updater_id']


def setup(args):
    """Derive a dedicated updater identity from an installed local role."""
    require_root()
    from .runtime import service_lock
    args = SimpleNamespace(**vars(args))
    args.home = str(trusted(args.home or '/var/lib/ctl-updater-'+args.kind, private=True))
    name = args.service_name or 'ctl-updater-'+args.kind+'.service'
    validate_service_name(name)
    args.cli_path = str(trusted(args.cli_path))
    args.agent_home = str(trusted(args.agent_home, private=True))
    args.server_home = str(trusted(args.server_home, private=True))
    release_version(installer_identity(args.cli_path)['version'])
    # Validate locally fixed paths before requesting any credential.
    if args.kind == 'node':
        identity = read_json(Path(args.agent_home)/'node.json')
        args.target_id = identity.get('node_id')
        validate_id(args.target_id)
        args.server = validate_origin(identity.get('server'))
    else:
        instance = server_bundle.read_instance(Path(args.server_home))
        args.server = validate_origin(instance.get('origin'))
        args.target_id = 'center'
    home = private_directory(args.home)
    trusted(home/'.setup.lock')
    with service_lock(home/'.setup.lock'):
        jobs = home/'jobs'
        trusted(jobs, private=True)
        if jobs.exists():
            for directory in jobs.iterdir():
                trusted(directory, private=True)
                if directory.name.startswith('.stage-'): continue
                record = read_json(directory/'journal.json')
                if not record.get('acknowledged') or record.get('report', {}).get('status') not in TERMINAL:
                    raise ValueError('unfinished updater journal prevents setup')
        existing = home/'updater.json'
        if existing.exists():
            config = read_json(existing)
            fields = ('kind', 'target_id', 'server', 'cli_path', 'agent_home', 'agent_service', 'server_home')
            if any(config.get(key) != getattr(args, key) for key in fields):
                raise ValueError('existing updater configuration differs from local installation')
            validate_id(config.get('updater_id'))
            validate_token(config.get('token'))
            if not trusted(home/'bootstrap.pyz', private=True).is_file():
                raise ValueError('updater bootstrap is missing')
            python = config.get('python')
            if not isinstance(python, str) or not Path(python).is_absolute() or not trusted(python).is_file():
                raise ValueError('updater Python executable is missing')
        else:
            if args.kind == 'node':
                client = PlatformClient(Credentials(args.server, validate_token(identity.get('token'))))
                invitation = client.json('POST', '/api/v1/node/updater-enrollment', {})
                transport = None
            else:
                keys = trusted(Path(args.server_home)/'keys', private=True, owners=(0,10001))
                credential = validate_token(read_platform_key(keys/'owner.token').decode('ascii').strip())
                transport = 'http://127.0.0.1:'+str(instance['api_port'])
                client = PlatformClient(Credentials(transport, credential))
                invitation = client.json('POST', '/api/v1/system-updates/enrollments', {'kind':'center','target_id':'center'})
            enroll(args, validate_token(invitation.get('enrollment_token')), transport=transport)
            config = read_json(existing)
        install_service(home, config, name)
    return config['updater_id']


def add_commands(sub):
    parser=sub.add_parser('updater',help='enroll or operate the independent root system updater')
    commands=parser.add_subparsers(dest='updater_command',required=True)
    for name in ('enroll','run','service','install','setup'):
        command=commands.add_parser(name); command.add_argument('--home',default=None if name=='setup' else DEFAULT_HOME)
        if name in ('enroll','setup'):
            command.add_argument('--kind',choices=('node','center'),required=True)
            command.add_argument('--cli-path',default='/usr/local/bin/ctl')
            command.add_argument('--agent-home',default='/var/lib/ctl-agent')
            command.add_argument('--agent-service',default='ctl-agent.service')
            command.add_argument('--server-home',default='/opt/ctl-platform')
        if name=='enroll':
            command.add_argument('--server',required=True)
            command.add_argument('--target-id',required=True)
            command.add_argument('--token-file',help='private enrollment token file; default hidden prompt')
        if name=='setup': command.add_argument('--service-name')
        if name=='run': command.add_argument('--once',action='store_true')
        if name=='install': command.add_argument('--service-name',default='ctl-updater.service')


def validate_service_name(name):
    if not isinstance(name,str) or not re.fullmatch(r'ctl-updater(?:-[A-Za-z0-9_-]{1,48})?\.service',name):
        raise ValueError('updater service name must be ctl-updater[-suffix].service')
    return name


def install_service(home,config,name):
    validate_service_name(name)
    unit=trusted(Path('/etc/systemd/system')/name)
    content=service_unit(home).encode()
    if unit.exists():
        if not unit.is_file() or unit.read_bytes()!=content:
            raise ValueError('existing systemd unit differs; choose a separate updater service name')
    else:
        bootstrap.atomic_write(unit,content,0o644)
    host=Host(config); host.command(['systemctl','daemon-reload'])
    host.command(['systemctl','enable','--now',name],timeout=60)
    host.command(['systemctl','is-active','--quiet',name])


def dispatch(args):
    require_root()
    if args.updater_command=='setup':
        setup(args)
        print('OK: automatic updates configured and started')
        return
    home=trusted(args.home,private=True)
    if args.updater_command=='enroll':
        token=read_private(trusted(args.token_file,private=True)).decode().strip() if args.token_file else getpass.getpass('Enrollment token: ')
        identifier=enroll(args,token)
        print('OK: updater enrolled '+identifier+'; run ctl updater install --home '+str(home))
        return
    config=read_json(home/'updater.json')
    trusted(home/'bootstrap.pyz',private=True)
    if args.updater_command=='service': print(service_unit(home),end=''); return
    if args.updater_command=='install':
        install_service(home,config,args.service_name)
        print('OK: '+args.service_name+' installed and started'); return
    from .runtime import service_lock
    trusted(home/'.lock')
    stop=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stop.set()); signal.signal(signal.SIGINT,lambda *_:stop.set())
    with service_lock(home/'.lock'):
        client=PlatformClient(Credentials(config['server'],config['token']))
        worker=Updater(home,config,client,Host(config))
        while not stop.is_set():
            try: worker.run_once()
            except (ValueError,RuntimeError,OSError,subprocess.SubprocessError):
                print('Updater unavailable; retained private journal for recovery.',file=sys.stderr)
            if args.once: break
            stop.wait(15)
