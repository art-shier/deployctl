"""Static deployment types and Linux destination validation."""
import posixpath
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .contract import validate_name,version,integer
from .runtime_snapshot import reject_links,validate_id


def validate_deployment_type(value=None):
    if value is None or value == '': return 'docker'
    if value not in ('docker', 'static'): raise ValueError('invalid project deployment type')
    return value


def validate_target_dir(value):
    if not isinstance(value, str): raise ValueError('target directory must be a Linux absolute path')
    try: size = len(value.encode('utf-8'))
    except UnicodeError: raise ValueError('invalid target directory encoding') from None
    if (not value.startswith('/') or value == '/' or posixpath.normpath(value) != value
            or value.startswith('//') or '\\' in value or size > 4096
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 or c in '\u2028\u2029' for c in value)):
        raise ValueError('target directory must be a normalized Linux absolute path other than /')
    return value


def validate_static_ref(value,app,env):
    if value is None:return None
    if not isinstance(value,dict) or set(value)!={'version','package_sha256','tree_sha256','archive_format','management_source'}:
        raise ValueError('invalid static deployment reference')
    version(value['version'])
    for field in ('package_sha256','tree_sha256'):
        if not isinstance(value[field],str) or not re.fullmatch('[a-f0-9]{64}',value[field]):raise ValueError('invalid static digest')
    if value['archive_format'] not in ('zip','tar.gz'):raise ValueError('invalid static archive format')
    source=value['management_source']
    if not isinstance(source,dict) or set(source)!={'project','environment','release_id','revision_id','origin'} or source['project']!=app or source['environment']!=env:
        raise ValueError('invalid static management source')
    validate_id(source['release_id']);validate_id(source['revision_id'])
    if source['origin'] is not None:
        from .platform_client import validate_origin
        validate_origin(source['origin'])
    return copy.deepcopy(value)


def normalize_static_state(data,app,env):
    validate_name(app);validate_name(env,'environment',32)
    if data is None:return {'schema_version':1,'deployment_type':'static','application':app,'environment':env,
                           'current':None,'previous':None,'transaction':None,'target_dir':None}
    fields={'schema_version','deployment_type','application','environment','current','previous','transaction','target_dir'}
    if not isinstance(data,dict) or not fields<=set(data) or set(data)-fields-{'updated_at'} or type(data['schema_version']) is not int or data['schema_version']!=1 or data['deployment_type']!='static' or data['application']!=app or data['environment']!=env:
        raise ValueError('invalid or mismatched static deployment state')
    if data['target_dir'] is not None:validate_target_dir(data['target_dir'])
    result=copy.deepcopy(data)
    for field in ('current','previous'):result[field]=validate_static_ref(data[field],app,env)
    tx=data['transaction']
    if tx is not None:
        if not isinstance(tx,dict) or set(tx)!={'from','to','phase','empty_identity'} or tx['phase'] not in ('prepared','switched','rollback'):
            raise ValueError('invalid static pending transaction')
        validate_static_ref(tx['from'],app,env)
        if validate_static_ref(tx['to'],app,env) is None:raise ValueError('missing static candidate')
        identity=tx['empty_identity']
        if identity is not None:
            if not isinstance(identity,dict) or set(identity)!={'device','inode'}:raise ValueError('invalid original directory identity')
            for field in ('device','inode'):integer(identity[field],0,2**64-1,'directory identity')
    if (result['current'] or result['previous'] or tx) and result['target_dir'] is None:raise ValueError('missing static target binding')
    return result


def tree_bytes(manifest):
    return json.dumps(manifest,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')


def build_tree_manifest(files):
    from .static_archive import MAX_EXPANDED,MAX_FILE,MAX_ENTRIES,archive_name
    files=Path(files);reject_links(files)
    if not files.is_dir():raise ValueError('missing static file tree')
    result=[];total=0
    for parent,dirs,names in os.walk(files,followlinks=False):
        for name in sorted(dirs+names):
            path=Path(parent)/name;reject_links(path);entry=path.lstat()
            relative=path.relative_to(files).as_posix();directory=stat.S_ISDIR(entry.st_mode)
            archive_name(relative,directory)
            if not directory and not stat.S_ISREG(entry.st_mode):raise ValueError('static tree contains non-regular files')
            mode=entry.st_mode&0o7777
            if mode!=(0o755 if directory else 0o644):raise ValueError('static tree permissions changed')
            row={'path':relative,'type':'directory' if directory else 'file'}
            if not directory:
                if entry.st_size>MAX_FILE:raise ValueError('static tree file exceeds limit')
                total+=entry.st_size
                if total>MAX_EXPANDED:raise ValueError('static tree exceeds limit')
                digest=hashlib.sha256()
                with path.open('rb') as source:
                    for block in iter(lambda:source.read(65536),b''):digest.update(block)
                row.update(size=entry.st_size,sha256=digest.hexdigest())
            result.append(row)
            if len(result)>MAX_ENTRIES:raise ValueError('static tree has too many entries')
    result.sort(key=lambda row:row['path'].encode('utf-8'))
    if not any(row['type']=='file' for row in result):raise ValueError('empty static file tree')
    return {'schema_version':1,'entries':result}


def verify_tree(files,manifest):
    if build_tree_manifest(files)!=manifest:raise ValueError('static file tree has been modified')
