"""Bounded same-origin platform protocol with fixed immutable resolutions."""
from contextlib import contextmanager
import base64
import hashlib
import json
import http.client
import os
from pathlib import Path
import re
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import __version__
from .contract import DIGEST_IMAGE, integer, mapping, validate_name, version
from .release import MAX_PACKAGE
from .runtime_config import validate_values
from .runtime_snapshot import validate_id
from .static_state import validate_target_dir, validate_deployment_type


def validate_origin(value):
    if not isinstance(value,str) or any(ord(c)<=32 or ord(c)==127 for c in value) or '\\' in value:
        raise ValueError('platform server must be an HTTPS origin')
    u = urlsplit(value)
    if (not u.hostname or u.username or u.password or u.path not in ('','/') or u.query or u.fragment
            or (u.scheme!='https' and not (u.scheme=='http' and u.hostname in ('localhost','127.0.0.1','::1')))):
        raise ValueError('platform server must be an HTTPS origin; HTTP is limited to loopback tests')
    try: u.port
    except ValueError: raise ValueError('invalid platform port') from None
    return value.rstrip('/')


def validate_token(value):
    if not isinstance(value,str) or not 16<=len(value)<=4096 or not re.fullmatch(r'[A-Za-z0-9_-]+',value):
        raise ValueError('invalid platform token')
    return value


def validate_defaults(value):
    mapping(value, {'host_port','bind_address','memory_limit','cpus'}, set(), 'deployment defaults')
    from .contract import validate_deployment
    container = {'port':8080}
    for name in ('host_port','bind_address'):
        if name in value: container[name] = value[name]
    resources = {k:v for k,v in value.items() if k in ('memory_limit','cpus')}
    validate_deployment({'schema_version':1,'application':'check','container':container,'health':{'readiness_path':'/ready'},'resources':resources})
    return dict(value)


def validate_resolution(value, project, environment=None, requested_version=None):
    static=isinstance(value,dict) and type(value.get('schema_version')) is int and value['schema_version']==2
    fields={'schema_version','minimum_client_version','project','environment','release','configuration'}
    if static:fields.add('deployment_type')
    mapping(value,fields,fields,'resolution')
    if type(value['schema_version']) is not int or value['schema_version'] not in (1,2) or value['project']!=project or static and value['deployment_type']!='static':
        raise ValueError('unsupported or mismatched resolution')
    minimum = value['minimum_client_version']
    if not isinstance(minimum,str) or not re.fullmatch(r'\d+\.\d+\.\d+', minimum) or tuple(map(int,minimum.split('.')))>tuple(map(int,__version__.split('.'))):
        raise ValueError('platform requires a newer CLI')
    validate_name(value['environment'],'environment',32)
    if environment and value['environment']!=environment: raise ValueError('resolution environment mismatch')
    release_fields={'id','version','package_path','sha256'}
    release_fields.update({'archive_format','size','expanded_size','entry_count','commit'} if static else {'image'})
    release = mapping(value['release'],release_fields,release_fields,'release')
    validate_id(release['id']); version(release['version'])
    if requested_version and release['version']!=requested_version: raise ValueError('resolution version mismatch')
    if not static and (not isinstance(release['image'],str) or not DIGEST_IMAGE.fullmatch(release['image'])): raise ValueError('invalid resolved image')
    if release['package_path']!=f"/api/v1/projects/{project}/artifacts/{release['id']}": raise ValueError('invalid artifact path')
    if not isinstance(release['sha256'],str) or not re.fullmatch('[a-f0-9]{64}', release['sha256']): raise ValueError('invalid checksum')
    if static:
        from .static_archive import MAX_PACKAGE as STATIC_MAX, MAX_EXPANDED, MAX_ENTRIES
        if release['archive_format'] not in ('zip','tar.gz'):raise ValueError('invalid static archive format')
        integer(release['size'],1,STATIC_MAX,'static package size')
        integer(release['expanded_size'],0,MAX_EXPANDED,'static expanded size')
        integer(release['entry_count'],1,MAX_ENTRIES,'static entry count')
        if not isinstance(release['commit'],str) or release['commit'] and not re.fullmatch(r'[a-f0-9]{40,64}',release['commit']):raise ValueError('invalid source commit')
        cfg=mapping(value['configuration'],{'id','revision','deployment_defaults'},{'id','revision','deployment_defaults'},'static configuration')
        validate_id(cfg['id']);integer(cfg['revision'],1,2**63-1,'configuration revision')
        defaults=mapping(cfg['deployment_defaults'],{'target_dir'},set(),'static defaults')
        if 'target_dir' in defaults:validate_target_dir(defaults['target_dir'])
        return value
    cfg = mapping(value['configuration'], {'id','revision','runtime_env','install_params','deployment_defaults'}, {'id','revision','runtime_env','install_params','deployment_defaults'}, 'configuration')
    validate_id(cfg['id']); integer(cfg['revision'],1,2**63-1,'configuration revision')
    validate_values(cfg['runtime_env'],'managed runtime'); validate_values(cfg['install_params'],'managed install parameters',False)
    if {'BASH_ENV','ENV','BASHOPTS','SHELLOPTS'}.intersection(cfg['runtime_env']): raise ValueError('managed runtime contains startup controls')
    validate_defaults(cfg['deployment_defaults'])
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None


class PlatformError(RuntimeError):
    def __init__(self,status):
        self.status=status
        message = ('conflict: reload current metadata/revision before retrying; '
                   'a name may already exist or a group may still contain projects'
                   if status == 409 else 'check permissions, project, environment and version')
        super().__init__(f'platform HTTP {status}; {message}')


def validate_registry_token(token):
    if not isinstance(token,str) or not token or token.startswith('ctl_') or len(token)>12288 or not re.fullmatch(r'[A-Za-z0-9._~+/=-]+',token):
        raise ValueError('invalid external registry verification token')
    return token


class PlatformClient:
    def __init__(self, credentials):
        self.server = validate_origin(credentials.server)
        self.token = validate_token(credentials.token)

    def request(self, method, path, value=None, raw=None, content_type=None, limit=512*1024, basic=False, verification_token=None, download_progress=None, expected_status=None, body_length=None):
        if not isinstance(path,str) or not path.startswith('/') or path.startswith('//') or '\\' in path or any(ord(c)<=32 for c in path):
            raise ValueError('invalid platform API path')
        headers = {'Accept':'application/json','User-Agent':f'deployctl/{__version__}'}
        headers['Authorization'] = ('Basic '+base64.b64encode(('ctl:'+self.token).encode()).decode()) if basic else 'Bearer '+self.token
        if verification_token is not None: headers['X-Registry-Verification-Token']=validate_registry_token(verification_token)
        if value is not None: raw=json.dumps(value).encode(); content_type='application/json'
        if content_type: headers['Content-Type']=content_type
        if body_length is not None:headers['Content-Length']=str(body_length)
        try:
            with build_opener(NoRedirect()).open(Request(self.server+path,data=raw,headers=headers,method=method),timeout=60) as response:
                if expected_status is not None and response.status != expected_status:
                    raise ValueError('unexpected platform response status')
                if download_progress is not None:
                    from .download import read_response
                    return read_response(response, limit, download_progress)
                data=response.read(limit+1)
                if len(data)>limit: raise ValueError('platform response exceeds limit')
                return data
        except HTTPError as exc: raise PlatformError(exc.code) from None
        except (URLError,TimeoutError,OSError): raise RuntimeError('platform connection failed; check server and TLS') from None

    def json(self,*args,**kwargs):
        try: return json.loads(self.request(*args,**kwargs))
        except (UnicodeError,json.JSONDecodeError): raise ValueError('invalid platform response') from None

    def resolve(self, project, environment=None, version=None):
        validate_name(project)
        body={}
        if environment: validate_name(environment,'environment',32); body['environment']=environment
        if version: globals()['version'](version); body['version']=version
        return validate_resolution(self.json('POST',f'/api/v1/projects/{project}/resolve',body),project,environment,version)

    def download_release(self, resolution, cache, progress=None):
        if resolution.get('deployment_type')=='static':
            from .static_archive import inspect_static_archive,MAX_PACKAGE as STATIC_MAX
            validate_resolution(resolution,resolution['project'],resolution['environment'])
            release=resolution['release'];path=Path(cache)/('release.'+release['archive_format'])
            self.download_to_file(release['package_path'],path,release['sha256'],STATIC_MAX,progress)
            info=inspect_static_archive(path)
            if (info.archive_format,info.size,info.expanded_size,info.entry_count)!=(release['archive_format'],release['size'],release['expanded_size'],release['entry_count']):
                path.unlink();raise ValueError('resolved static metadata does not match package')
            return path
        options = {'download_progress': progress} if progress is not None else {}
        raw=self.request('GET',resolution['release']['package_path'],limit=MAX_PACKAGE,**options)
        if hashlib.sha256(raw).hexdigest()!=resolution['release']['sha256']: raise ValueError('platform package checksum mismatch')
        path=Path(cache)/'release.tar.gz';path.write_bytes(raw)
        from .release import unpack_release
        with tempfile.TemporaryDirectory(prefix='verify-',dir=cache) as folder:
            manifest=unpack_release(path,Path(folder)/'package')
        if (manifest['application'],manifest['version'],manifest['image'])!=(resolution['project'],resolution['release']['version'],resolution['release']['image']):
            raise ValueError('resolved release does not match package')
        return path

    def download_to_file(self,path,destination,expected_sha,max_bytes,progress=None):
        if not isinstance(path,str) or not path.startswith('/') or path.startswith('//') or '\\' in path or any(ord(c)<=32 for c in path):raise ValueError('invalid platform API path')
        if not isinstance(expected_sha,str) or not re.fullmatch('[a-f0-9]{64}',expected_sha):raise ValueError('invalid checksum')
        destination=Path(destination)
        from .runtime_snapshot import reject_links
        reject_links(destination);destination.parent.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(prefix='.download-',dir=destination.parent)
        try:
            with os.fdopen(fd,'wb') as output:
                headers={'Authorization':'Bearer '+self.token,'User-Agent':f'deployctl/{__version__}','Accept':'application/octet-stream'}
                with build_opener(NoRedirect()).open(Request(self.server+path,headers=headers),timeout=60) as response:
                    if response.status!=200:raise ValueError('unexpected artifact response status')
                    length=response.headers.get('Content-Length')
                    if length is not None:
                        if not re.fullmatch('[0-9]+',length) or int(length)>max_bytes:raise ValueError('invalid or excessive download length')
                        length=int(length)
                    actual=0;digest=hashlib.sha256()
                    if progress:progress.downloaded(0,length)
                    while True:
                        data=response.read(min(65536,max_bytes+1-actual))
                        if not data:break
                        actual+=len(data)
                        if actual>max_bytes:raise ValueError('download exceeds size limit')
                        digest.update(data);output.write(data)
                        if progress:progress.downloaded(actual,length)
                    if length is not None and actual!=length:raise ValueError('download incomplete')
                    if digest.hexdigest()!=expected_sha:raise ValueError('platform package checksum mismatch')
                    output.flush();os.fsync(output.fileno())
            if destination.exists():raise ValueError('download destination already exists')
            os.replace(name,destination)
            return destination
        except HTTPError as exc:raise PlatformError(exc.code) from None
        except (URLError,TimeoutError,http.client.IncompleteRead,OSError):raise RuntimeError('platform download failed; check server and TLS') from None
        finally:
            if os.path.exists(name):os.unlink(name)

    def publish(self, project, release_version, package, channel=None, verification_token=None,commit=None):
        validate_name(project);version(release_version)
        if channel not in (None,'stable'):raise ValueError('invalid release channel')
        if commit is not None and (not isinstance(commit,str) or commit and not re.fullmatch(r'[a-f0-9]{40,64}',commit)):raise ValueError('invalid source commit')
        project_meta=self.json('GET',f'/api/v1/projects/{project}')
        if not isinstance(project_meta,dict) or project_meta.get('slug')!=project:raise ValueError('mismatched project metadata')
        static=validate_deployment_type(project_meta.get('deployment_type'))=='static'
        if static and verification_token is not None:raise ValueError('static projects do not use a Registry token')
        from .static_archive import MAX_PACKAGE as STATIC_MAX,inspect_static_archive
        path=Path(package)
        maximum=STATIC_MAX if static else MAX_PACKAGE
        if path.stat().st_size>maximum:raise ValueError('package exceeds size limit')
        with tempfile.TemporaryDirectory(prefix='ctl-publish-') as folder:
            staged=Path(folder)/'package';digest=hashlib.sha256();size=0
            with path.open('rb') as source,staged.open('xb') as output:
                while True:
                    block=source.read(65536)
                    if not block:break
                    size+=len(block)
                    if size>maximum:raise ValueError('package exceeds size limit')
                    output.write(block);digest.update(block)
            format_name=inspect_static_archive(staged).archive_format if static else 'tar.gz'
            boundary='ctl-'+os.urandom(24).hex();fields={'version':release_version,'sha256':digest.hexdigest()}
            if channel:fields['channel']=channel
            if commit is not None:fields['commit']=commit
            prefix=b''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{val}\r\n'.encode() for name,val in fields.items())
            prefix+=f'--{boundary}\r\nContent-Disposition: form-data; name="package"; filename="release.{format_name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
            suffix=f'\r\n--{boundary}--\r\n'.encode()
            def body():
                yield prefix
                with staged.open('rb') as source:
                    for block in iter(lambda:source.read(65536),b''):yield block
                yield suffix
            return self.json('POST',f'/api/v1/projects/{project}/releases',raw=body(),body_length=len(prefix)+size+len(suffix),content_type=f'multipart/form-data; boundary={boundary}',verification_token=verification_token)

    def recover_release(self, project, release_version, output, commit=None):
        """Publisher can recover immutable artifacts without reading production config."""
        validate_name(project);version(release_version)
        try: record=self.json('GET',f'/api/v1/projects/{project}/releases/{release_version}')
        except PlatformError as exc:
            if exc.status==404:return None
            raise
        if not isinstance(record,dict) or record.get('project')!=project or record.get('version')!=release_version or record.get('status')!='published':raise ValueError('published release cannot be resumed')
        validate_id(record.get('id'))
        if commit is not None and record.get('commit')!=commit:raise ValueError('registered release belongs to a different source commit')
        if validate_deployment_type(record.get('deployment_type'))=='static':
            release={k:record.get(k) for k in ('id','version','sha256','archive_format','size','expanded_size','entry_count','commit')}
            release['package_path']=f"/api/v1/projects/{project}/artifacts/{release['id']}"
            resolution={'schema_version':2,'minimum_client_version':'1.13.0','deployment_type':'static','project':project,'environment':'recovery','release':release,
                        'configuration':{'id':'0'*32,'revision':1,'deployment_defaults':{}}}
            validate_resolution(resolution,project,'recovery',release_version)
            target=Path(output);target.mkdir(parents=True,exist_ok=True)
            destination=target/f"{project}-{release_version}.{release['archive_format']}"
            from .runtime_snapshot import reject_links
            reject_links(destination)
            if destination.exists():
                digest=hashlib.sha256()
                with destination.open('rb') as source:
                    for block in iter(lambda:source.read(65536),b''):digest.update(block)
                if digest.hexdigest()!=release['sha256']:raise ValueError('recovery output contains a different package')
            else:
                with tempfile.TemporaryDirectory(prefix='ctl-resume-') as cache:
                    package=self.download_release(resolution,cache)
                    with package.open('rb') as source,destination.open('xb') as dest:
                        for block in iter(lambda:source.read(65536),b''):dest.write(block)
            sidecar=destination.with_name(destination.name+'.sha256');reject_links(sidecar)
            sidecar.write_text(f"{release['sha256']}  {destination.name}\n",encoding='ascii')
            return record
        release={k:record.get(k) for k in ('id','version','image','sha256')}
        release['package_path']=f"/api/v1/projects/{project}/artifacts/{release['id']}"
        resolution={'schema_version':1,'minimum_client_version':'1.7.0','project':project,'environment':'recovery',
                    'release':release,'configuration':{'id':'0'*32,'revision':1,'runtime_env':{},'install_params':{},'deployment_defaults':{}}}
        validate_resolution(resolution,project,'recovery',release_version)
        with tempfile.TemporaryDirectory(prefix='ctl-resume-') as cache:
            path=self.download_release(resolution,cache)
            from .contract import read_yaml
            from .release import unpack_release
            manifest=unpack_release(path,Path(cache)/'commit-check')
            if commit is not None and manifest.get('commit')!=commit:raise ValueError('published package source commit mismatch')
            target=Path(output);target.mkdir(parents=True,exist_ok=True)
            destination=target/f'{project}-{release_version}.tar.gz'
            if destination.exists() and destination.read_bytes()!=path.read_bytes():raise ValueError('recovery output contains a different package')
            destination.write_bytes(path.read_bytes())
            destination.with_name(destination.name+'.sha256').write_text(f"{release['sha256']}  {destination.name}\n",encoding='ascii')
        return record

    @contextmanager
    def registry_config(self, image):
        identity=self.json('GET','/api/v1/me')
        host,repository=image.split('@')[0].split('/',1)
        if identity.get('registry_host')!=host:
            yield None; return
        credential=self.json('GET','/registry/token?'+urlencode({'service':'ctl-registry','scope':f'repository:{repository}:pull'}),basic=True)
        token=credential.get('token')
        if not isinstance(token,str) or not token: raise ValueError('invalid registry credential')
        with tempfile.TemporaryDirectory(prefix='ctl-registry-') as folder:
            os.chmod(folder,0o700)
            path=Path(folder)/'config.json'
            path.write_text(json.dumps({'auths':{host:{'registrytoken':token}}}),encoding='utf-8');path.chmod(0o600)
            yield folder
