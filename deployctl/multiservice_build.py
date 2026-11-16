"""Validated multi-image build plans and digest-preserving registry delivery."""
import hashlib
import json
from pathlib import Path
import re

from .contract import DIGEST_IMAGE, version
from .multiservice_contract import validate_multiservice_deployment

REPOSITORY = re.compile(r'[a-z0-9.-]+(?::[0-9]+)?/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*\Z')
DIGEST = re.compile(r'sha256:[a-f0-9]{64}\Z')
INDEX_TYPES = {'application/vnd.oci.image.index.v1+json','application/vnd.docker.distribution.manifest.list.v2+json'}
IMAGE_TYPES = {'application/vnd.oci.image.manifest.v1+json','application/vnd.docker.distribution.manifest.v2+json'}


def component_build_args(config, raw=''):
    """Explicit component-keyed ordinary build values; never shell interpolation."""
    from .contract import validate_build_args
    config = validate_multiservice_deployment(config)
    if not isinstance(raw,str) or len(raw.encode()) > 65536:
        raise ValueError('service-build-args must be JSON of at most 64 KiB')
    def unique(pairs):
        result = {}
        for key,value in pairs:
            if key in result: raise ValueError('duplicate service build argument key')
            result[key] = value
        return result
    extra = json.loads(raw,object_pairs_hook=unique) if raw.strip() else {}
    if not isinstance(extra,dict) or len(extra)>128: raise ValueError('invalid service-build-args')
    nodes = {**config['services'],**config['jobs']}
    for name,args in extra.items():
        if name not in nodes or 'build' not in nodes[name]:
            raise ValueError('service-build-args must select a declared build component')
        build = nodes[name]['build']
        build['args'] = validate_build_args({**build.get('args',{}),**validate_build_args(args)})
    return validate_multiservice_deployment(config)


def delivery_plan(config, root, repository, release_version):
    config = validate_multiservice_deployment(config); version(release_version)
    if not isinstance(repository,str) or not REPOSITORY.fullmatch(repository): raise ValueError('invalid project image repository')
    root = Path(root).resolve(); nodes = {}
    for name, spec in {**config['services'],**config['jobs']}.items():
        tag = release_version[:60]+'-'+name[:32]+'-'+hashlib.sha256((release_version+'/'+name).encode()).hexdigest()[:12]
        entry = {'tag':repository+':'+tag}
        if 'build' in spec:
            build = spec['build']
            for key in ('context','dockerfile'):
                path = (root/build[key]).resolve()
                if not path.is_relative_to(root) or not path.exists(): raise ValueError('build paths must exist inside the project')
                if key == 'context' and not path.is_dir() or key == 'dockerfile' and not path.is_file(): raise ValueError('invalid build path type')
            entry.update(kind='build',build=build)
        else:
            if spec['image'].split('@')[0] != repository and spec.get('mirror') != 'project':
                raise ValueError('external image requires mirror: project')
            entry.update(kind='mirror',source=spec['image'])
        nodes[name] = entry
    return {'application':config['application'],'platform':config['platform'],'repository':repository,'nodes':nodes}


def verify_manifest_tree(reference, fetch_raw, max_bytes=4*1024**3):
    if not isinstance(reference,str) or not DIGEST_IMAGE.fullmatch(reference): raise ValueError('pinned image required')
    repository, root_digest = reference.split('@'); visited = set(); blobs = {}; manifests = {}
    def descriptor(item):
        if not isinstance(item,dict) or item.get('urls'): raise ValueError('external blob URLs are not allowed')
        digest, size = item.get('digest'), item.get('size')
        if not isinstance(digest,str) or not DIGEST.fullmatch(digest) or type(size) is not int or not 0 <= size <= max_bytes:
            raise ValueError('invalid descriptor digest/size')
        return digest,size
    def visit(digest, depth=0, expected_size=None):
        if depth > 2 or len(visited) >= 32: raise ValueError('manifest tree exceeds limits')
        if digest in visited:
            if expected_size is not None and manifests[digest] != expected_size: raise ValueError('manifest size mismatch')
            return
        visited.add(digest)
        raw = fetch_raw(repository+'@'+digest)
        if not isinstance(raw,bytes) or len(raw) > 2*1024**2: raise ValueError('manifest size limit exceeded')
        if expected_size is not None and len(raw) != expected_size: raise ValueError('manifest size mismatch')
        if 'sha256:'+hashlib.sha256(raw).hexdigest() != digest: raise ValueError('manifest digest mismatch')
        manifests[digest] = len(raw)
        body = json.loads(raw)
        if not isinstance(body,dict) or type(body.get('schemaVersion')) is not int or body['schemaVersion'] != 2: raise ValueError('unsupported manifest')
        if body.get('mediaType') in INDEX_TYPES:
            children = body.get('manifests')
            if not isinstance(children,list) or not 1 <= len(children) <= 32: raise ValueError('invalid manifest list')
            for item in children:
                child,size = descriptor(item); visit(child,depth+1,size)
        elif body.get('mediaType') in IMAGE_TYPES:
            layers = body.get('layers')
            if not isinstance(layers,list) or len(layers)>256: raise ValueError('invalid manifest layers')
            for item in [body.get('config'),*layers]:
                blob,size = descriptor(item)
                if blob in blobs and blobs[blob] != size: raise ValueError('inconsistent blob size')
                blobs[blob] = size
            if sum(blobs.values()) > max_bytes: raise ValueError('image size limit exceeded')
        else: raise ValueError('unsupported manifest media type')
    visit(root_digest)
    return {'bytes':sum(blobs.values()),'manifests':len(visited)}


def run_command(args, timeout=600):
    import subprocess
    import tempfile
    # File-backed output avoids unbounded Python buffers. Commands do not receive
    # passwords in argv, and copy/build use quiet output modes.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        result = subprocess.run(args, stdout=stdout, stderr=stderr, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f'{args[0]} {args[1]} failed (exit {result.returncode})')
        if stdout.tell() > 8*1024**2: raise ValueError('command output size exceeded')
        stdout.seek(0)
        return stdout.read()


class ImageTools:
    def __init__(self, root, authfile, runner=run_command, allow_loopback_http=False):
        self.root, self.authfile = Path(root).resolve(), str(Path(authfile).resolve())
        self.run, self.allow_loopback_http = runner, allow_loopback_http

    def tls(self, reference):
        host = reference.split('/')[0]
        return 'false' if self.allow_loopback_http and re.fullmatch(r'127\.0\.0\.1:[0-9]+',host) else 'true'

    def inspect(self, reference, platform, raw=False):
        os_name, architecture = platform.split('/')
        args = ['skopeo','--command-timeout','2m','--override-os',os_name,'--override-arch',architecture,
                'inspect','--authfile',self.authfile,'--tls-verify='+self.tls(reference),
                '--raw' if raw else '--config','docker://'+reference]
        result = self.run(args,timeout=130)
        if len(result) > 2*1024**2: raise ValueError('manifest/config size limit exceeded')
        return result

    def verify(self, reference, platform):
        result = verify_manifest_tree(reference,lambda ref:self.inspect(ref,platform,raw=True))
        config = json.loads(self.inspect(reference,platform))
        if not isinstance(config,dict) or (config.get('os'),config.get('architecture')) != tuple(platform.split('/')):
            raise ValueError('image platform differs from deployment platform')
        return result

    def mirror(self, source, target, platform):
        import tempfile
        repository, separator, tag = target.rpartition(':')
        if not separator or not REPOSITORY.fullmatch(repository) or not re.fullmatch(r'[\w][\w.-]{0,127}',tag):
            raise ValueError('invalid mirror destination')
        self.verify(source,platform)
        with tempfile.TemporaryDirectory(prefix='ctl-image-copy-') as temp:
            digestfile = Path(temp)/'digest'
            self.run(['skopeo','--command-timeout','10m','copy','--quiet','--all','--preserve-digests',
                '--src-authfile',self.authfile,'--dest-authfile',self.authfile,
                '--src-tls-verify='+self.tls(source),'--dest-tls-verify='+self.tls(target),
                '--digestfile',str(digestfile),'docker://'+source,'docker://'+target],timeout=610)
            if digestfile.stat().st_size > 128: raise ValueError('invalid copied digest')
            digest = digestfile.read_text().strip()
        if digest != source.split('@')[1]: raise ValueError('copied image digest changed')
        resolved = repository+'@'+digest
        self.verify(resolved,platform)
        return resolved

    def build(self, spec, target, platform):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='ctl-image-build-') as temp:
            metadata = Path(temp)/'metadata.json'
            args = ['docker','buildx','build','--quiet','--push','--provenance=false','--platform',platform,
                    '--tag',target,'--file',str(self.root/spec['dockerfile']),'--metadata-file',str(metadata)]
            for key,value in sorted(spec.get('args',{}).items()): args.extend(['--build-arg',key+'='+value])
            args.append(str(self.root/spec['context']))
            self.run(args,timeout=1800)
            if metadata.stat().st_size > 2*1024**2: raise ValueError('build metadata size exceeded')
            digest = json.loads(metadata.read_text()).get('containerimage.digest')
        if not isinstance(digest,str) or not DIGEST.fullmatch(digest): raise ValueError('missing immutable build digest')
        reference = target.rsplit(':',1)[0]+'@'+digest
        self.verify(reference,platform)
        return reference


def deliver_images(config, root, repository, release_version, tools):
    plan = delivery_plan(config,root,repository,release_version)
    images, completed = {}, {}
    for name,node in plan['nodes'].items():
        identity = json.dumps({k:v for k,v in node.items() if k != 'tag'},sort_keys=True)
        if identity not in completed:
            if node['kind'] == 'build':
                resolved = tools.build(node['build'],node['tag'],plan['platform'])
            else:
                resolved = tools.mirror(node['source'],node['tag'],plan['platform'])
            if not DIGEST_IMAGE.fullmatch(resolved) or resolved.split('@')[0] != repository:
                raise ValueError('resolved image escaped project repository')
            completed[identity] = resolved
        images[name] = completed[identity]
    return images
