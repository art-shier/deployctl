"""Linux target ownership and durable atomic symlink switching."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .runtime import atomic_json
from .runtime_snapshot import reject_links
from .static_state import validate_target_dir

CACHE_MARKER='.ctl-static-cache.json'


def reject_managed_cache_path(path,allowed_root=None):
    """Protect physical caches even when another manager uses a different root."""
    path=Path(path)
    for parent in (path,*path.parents):
        marker=parent/CACHE_MARKER
        if not os.path.lexists(marker):continue
        if allowed_root is not None and parent==Path(allowed_root):
            reject_links(marker)
            if not marker.is_file() or marker.stat().st_size>65536:raise ValueError('invalid managed static cache marker')
            try:value=json.loads(marker.read_text(encoding='utf-8'))
            except (UnicodeError,json.JSONDecodeError):raise ValueError('invalid managed static cache marker') from None
            if value!={'schema_version':1,'root':str(parent)}:raise ValueError('managed static cache marker changed')
            return
        raise ValueError('path overlaps an existing managed static cache')


def mark_cache_root(root):
    root=Path(root);reject_managed_cache_path(root,allowed_root=root)
    marker=root/CACHE_MARKER;reject_links(marker)
    if not marker.exists():
        atomic_json(marker,{'schema_version':1,'root':str(root)})
        marker.chmod(0o600);sync_dir(root)


def sync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


@contextmanager
def target_lock(target):
    if os.name!='posix':raise RuntimeError('static deployment requires Linux')
    import fcntl
    # A read-only directory descriptor gives all local users the same advisory
    # namespace, without writing a global file or following /tmp lock links.
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('another static deployment operation is running') from None
        yield
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)


def metadata_paths(target):
    target=Path(target)
    key=hashlib.sha256(str(target).encode('utf-8')).hexdigest()
    folder=target.parent/'.ctl-static'
    return folder,folder/(key+'.json'),folder/('empty-'+key)


def owner_record(target):
    _,path,_=metadata_paths(target);reject_links(path)
    if not path.exists():return None
    if not path.is_file() or path.stat().st_size>65536:raise ValueError('invalid static ownership record')
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (UnicodeError,json.JSONDecodeError):raise ValueError('invalid static ownership record') from None


def validate_target(target,root,config_root,owner):
    target=Path(validate_target_dir(str(target)))
    reject_links(target.parent)
    reject_managed_cache_path(target)
    if '.ctl-static' in target.parts:raise ValueError('target overlaps deployment ownership metadata')
    for managed in (Path(root),Path(config_root)):
        if target==managed or target.is_relative_to(managed) or managed.is_relative_to(target):raise ValueError('target overlaps managed deployment data')
    record=owner_record(target)
    if record is not None and record!=owner:raise ValueError('target directory belongs to another project/environment/root')
    if target.is_symlink():
        if record!=owner:raise ValueError('target is an unmanaged symlink')
    elif target.exists():
        if not target.is_dir() or any(target.iterdir()):raise ValueError('target directory must be empty before first installation')
        if record is not None:raise ValueError('managed target link has been replaced')


def claim_target(target,owner):
    folder,path,_=metadata_paths(target)
    reject_links(folder);folder.mkdir(mode=0o700,exist_ok=True);folder.chmod(0o700)
    existing=owner_record(target)
    if existing is not None and existing!=owner:raise ValueError('target directory belongs to another owner')
    atomic_json(path,owner);path.chmod(0o600);sync_dir(folder);sync_dir(target.parent)


def release_target(target,owner):
    _,path,_=metadata_paths(target)
    if owner_record(target)!=owner:raise ValueError('static ownership record changed')
    path.unlink();sync_dir(path.parent)


def switch_target(target,files):
    target=Path(target);files=Path(files)
    reject_links(target.parent);reject_links(files)
    if not files.is_dir():raise ValueError('missing static candidate tree')
    fd,name=tempfile.mkstemp(prefix='.ctl-switch-',dir=target.parent);os.close(fd);os.unlink(name)
    try:
        os.symlink(str(files),name);os.replace(name,target);sync_dir(target.parent)
    finally:
        if os.path.lexists(name):os.unlink(name)
