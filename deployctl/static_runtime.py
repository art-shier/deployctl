"""Static file preparation, target transactions, diagnostics and local rollback."""
import copy
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from .contract import validate_name
from .platform_client import validate_resolution
from .progress import Progress
from .runtime import atomic_json,service_lock
from .runtime_snapshot import reject_links
from .static_archive import extract_static_archive,inspect_static_archive,MAX_PACKAGE
from .static_state import normalize_static_state,build_tree_manifest,verify_tree,tree_bytes,validate_target_dir
from .static_target import target_lock,validate_target,switch_target,claim_target,release_target,owner_record,metadata_paths,sync_dir


def public_directories(path):
    path=Path(path);reject_links(path)
    missing=[]
    for part in (path,*path.parents):
        if not part.exists():missing.append(part)
    for part in reversed(missing):part.mkdir(mode=0o755);part.chmod(0o755)
    for part in (path,*path.parents):
        if not part.is_dir() or part.stat().st_mode&0o001==0:raise ValueError('static files need traversable parent directories; configure a public target/cache location')


class StaticManager:
    def __init__(self,root='/opt/deployments',config_root='/etc/deployctl',progress=None):
        self.root=Path(root).expanduser().absolute();self.config_root=Path(config_root).expanduser().absolute()
        reject_links(self.root);reject_links(self.config_root)
        self.root=self.root.resolve();self.config_root=self.config_root.resolve()
        self.progress=progress if progress is not None else Progress(enabled=False)

    def _home(self,app,env,create=False):
        validate_name(app);validate_name(env,'environment',32)
        home=self.root/app/env;reject_links(home)
        if create:public_directories(home/'releases')
        return home

    def _state(self,app,env):
        path=self._home(app,env)/'state.json';reject_links(path)
        if path.exists():
            if path.stat().st_size>65536:raise ValueError('static state exceeds limit')
            try:value=json.loads(path.read_text(encoding='utf-8'))
            except (UnicodeError,json.JSONDecodeError):raise ValueError('invalid static state') from None
        else:value=None
        return normalize_static_state(value,app,env)

    def _save(self,state,event):
        home=self._home(state['application'],state['environment'])
        normalize_static_state(state,state['application'],state['environment'])
        state['updated_at']=datetime.now(timezone.utc).isoformat()
        atomic_json(home/'state.json',state);sync_dir(home)
        try:
            path=home/'events.jsonl';reject_links(path)
            fd=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
            with os.fdopen(fd,'a',encoding='utf-8') as events:
                events.write(json.dumps({'time':state['updated_at'],'event':event,'version':state['current']['version'] if state['current'] else None,'pending':state['transaction'] is not None})+'\n')
        except OSError:pass

    def _folder(self,state,ref):
        return self._home(state['application'],state['environment'])/'releases'/(ref['version']+'-'+ref['package_sha256'])

    def _files(self,state,ref):
        return self._folder(state,ref)/'files'

    def _verify(self,state,ref):
        folder=self._folder(state,ref);reject_links(folder)
        path=folder/'manifest.json';reject_links(path)
        if not path.is_file() or path.stat().st_size>256*1024*1024:raise ValueError('missing or excessive static tree manifest')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=ref['tree_sha256']:raise ValueError('static tree manifest has been modified')
        try:manifest=json.loads(raw)
        except (UnicodeError,json.JSONDecodeError):raise ValueError('invalid static tree manifest') from None
        verify_tree(folder/'files',manifest)
        archive=folder/('package.'+ref['archive_format']);reject_links(archive)
        if not archive.is_file() or archive.stat().st_size>MAX_PACKAGE:raise ValueError('missing static package cache')
        digest=hashlib.sha256()
        with archive.open('rb') as source:
            for block in iter(lambda:source.read(65536),b''):digest.update(block)
        if digest.hexdigest()!=ref['package_sha256']:raise ValueError('static package cache has been modified')
        return folder/'files'

    def _owner(self,state):
        return {'schema_version':1,'root':str(self.root),'config_root':str(self.config_root),'project':state['application'],'environment':state['environment'],'target_dir':state['target_dir']}

    def _check_link(self,state,refs,allow_absent=False):
        target=Path(state['target_dir']);reject_links(target.parent)
        if owner_record(target)!=self._owner(state):raise ValueError('static target ownership record is missing or changed')
        if not os.path.lexists(target):
            if allow_absent:return
            raise ValueError('static target is missing')
        allowed={str(self._files(state,ref)) for ref in refs if ref}
        if not target.is_symlink() or os.readlink(target) not in allowed:raise ValueError('static target link has been changed')

    def _prepare(self,state,package,resolution,management_source):
        release=resolution['release'];info=inspect_static_archive(package)
        if (info.sha256,info.archive_format,info.size,info.expanded_size,info.entry_count)!=(release['sha256'],release['archive_format'],release['size'],release['expanded_size'],release['entry_count']):raise ValueError('static release metadata mismatch')
        source={'project':state['application'],'environment':state['environment'],'release_id':release['id'],'revision_id':resolution['configuration']['id'],'origin':None}
        if management_source is not None:source.update(management_source)
        ref={'version':release['version'],'package_sha256':info.sha256,'archive_format':info.archive_format,'tree_sha256':'0'*64,'management_source':source}
        folder=self._folder(state,ref)
        if folder.exists():
            manifest=folder/'manifest.json';reject_links(manifest)
            if not manifest.is_file() or manifest.stat().st_size>256*1024*1024:raise ValueError('invalid cached static release')
            known=next((old for old in (state['current'],state['previous']) if old and old['version']==ref['version'] and old['package_sha256']==ref['package_sha256']),None)
            if known is not None:ref['tree_sha256']=known['tree_sha256']
            else:
                # An obsolete cache has no authoritative state reference. Derive
                # its tree again from the verified archive, never from its own
                # potentially modified manifest.
                with tempfile.TemporaryDirectory(prefix='.verify-',dir=self._home(state['application'],state['environment'])) as temporary:
                    files=Path(temporary)/'files'
                    if extract_static_archive(package,files)!=info:raise ValueError('static package changed during preparation')
                    ref['tree_sha256']=hashlib.sha256(tree_bytes(build_tree_manifest(files))).hexdigest()
            self._verify(state,ref)
            return ref
        home=self._home(state['application'],state['environment'])
        stage=Path(tempfile.mkdtemp(prefix='.prepare-',dir=home))
        try:
            staged=stage/('package.'+info.archive_format)
            with Path(package).open('rb') as source_file,staged.open('xb') as dest:
                staged.chmod(0o600);size=0
                for block in iter(lambda:source_file.read(65536),b''):
                    size+=len(block)
                    if size>MAX_PACKAGE:raise ValueError('static package exceeds size limit')
                    dest.write(block)
                dest.flush();os.fsync(dest.fileno())
            extracted=extract_static_archive(staged,stage/'files')
            if extracted!=info:raise ValueError('static package changed during preparation')
            manifest=build_tree_manifest(stage/'files');raw=tree_bytes(manifest)
            ref['tree_sha256']=hashlib.sha256(raw).hexdigest()
            fd=os.open(stage/'manifest.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as output:output.write(raw);output.flush();os.fsync(output.fileno())
            # Persist file contents and every created directory before publication.
            for parent,dirs,names in os.walk(stage/'files',topdown=False):
                for name in names:
                    fd=os.open(Path(parent)/name,os.O_RDONLY)
                    try:os.fsync(fd)
                    finally:os.close(fd)
                sync_dir(parent)
            stage.chmod(0o755);sync_dir(stage);os.rename(stage,folder);sync_dir(folder.parent)
            self._verify(state,ref)
            return ref
        finally:
            if stage.exists():
                if not stage.is_relative_to(home) or stage.is_symlink():raise ValueError('unsafe static staging cleanup')
                shutil.rmtree(stage)

    def _restore_initial(self,state):
        target=Path(state['target_dir']);tx=state['transaction'];owner=self._owner(state)
        record=owner_record(target)
        if record is not None and record!=owner:raise ValueError('static target ownership changed during recovery')
        if target.is_symlink():
            self._check_link(state,[tx['to']]);target.unlink();sync_dir(target.parent)
        elif target.exists():
            identity=tx['empty_identity'];st=target.lstat()
            if identity is None or not target.is_dir() or any(target.iterdir()) or (st.st_dev,st.st_ino)!=(identity['device'],identity['inode']):raise ValueError('initial target has changed')
        _,_,backup=metadata_paths(target)
        if tx['empty_identity'] is not None:
            if backup.exists():
                st=backup.lstat();identity=tx['empty_identity']
                if backup.is_symlink() or not backup.is_dir() or any(backup.iterdir()) or (st.st_dev,st.st_ino)!=(identity['device'],identity['inode']):raise ValueError('original empty directory has changed')
                if os.path.lexists(target):raise ValueError('target appeared during recovery')
                os.rename(backup,target);sync_dir(target.parent)
            elif not target.exists():raise ValueError('original empty directory is missing')
        if record is not None:release_target(target,owner)

    def _recover(self,state):
        tx=state['transaction'];prior=tx['from']
        if prior:
            files=self._verify(state,prior);self._check_link(state,[prior,tx['to']]);switch_target(Path(state['target_dir']),files)
        else:self._restore_initial(state)
        result=copy.deepcopy(state);result['current']=prior;result['transaction']=None
        if prior is None:result['target_dir']=None;result['previous']=None
        self._save(result,'recover');return result

    def deploy(self,app,env,package,resolution,upgrade=False,target_dir=None,management_source=None):
        if os.name!='posix':raise RuntimeError('static deployment requires Linux')
        validate_resolution(resolution,app,env)
        if resolution.get('deployment_type')!='static':raise ValueError('static manager requires a static project')
        with target_lock(target_dir):
            home=self._home(app,env,create=True)
            with service_lock(home/'deploy.lock'):
                state=self._state(app,env)
                if state['transaction']:raise RuntimeError('pending static transaction; run ctl rollback first')
                if state['current'] and not upgrade:raise ValueError('application is already installed; use upgrade')
                if not state['current'] and upgrade:raise ValueError('application is not installed; use install')
                if target_dir is not None:validate_target_dir(target_dir)
                configured=resolution['configuration']['deployment_defaults'].get('target_dir')
                chosen=configured or target_dir or state['target_dir']
                if chosen is None:raise ValueError('set project deployment_defaults.target_dir or install with --target-dir')
                validate_target_dir(chosen)
                if state['target_dir'] is not None and chosen!=state['target_dir']:raise ValueError('target directory migration requires a separate operation')
                candidate_state=copy.deepcopy(state);candidate_state['target_dir']=chosen
                target=Path(chosen);public_directories(target.parent)
                owner=self._owner(candidate_state);validate_target(target,self.root,self.config_root,owner)
                if not state['current'] and owner_record(target) is not None:
                    raise ValueError('owned static target has no authoritative state; restore state from backup')
                if state['current']:
                    self._verify(state,state['current']);self._check_link(state,[state['current']])
                candidate=self._prepare(candidate_state,package,resolution,management_source)
                if state['current'] and state['current']['version']==candidate['version']:
                    if state['current']['package_sha256']!=candidate['package_sha256'] or state['current']['tree_sha256']!=candidate['tree_sha256']:raise ValueError('installed version has different content')
                    state['current']=candidate;self._save(state,'retry');return state
                identity=None
                if target.exists() and not target.is_symlink():
                    st=target.lstat();identity={'device':st.st_dev,'inode':st.st_ino}
                candidate_state['transaction']={'from':state['current'],'to':candidate,'phase':'prepared','empty_identity':identity}
                self._save(candidate_state,'prepared')
                try:
                    claim_target(target,owner)
                    if identity is not None:
                        st=target.lstat()
                        if target.is_symlink() or any(target.iterdir()) or (st.st_dev,st.st_ino)!=(identity['device'],identity['inode']):raise ValueError('target changed during preparation')
                        _,_,backup=metadata_paths(target);reject_links(backup)
                        if backup.exists():raise ValueError('old empty-directory recovery is pending')
                        os.rename(target,backup);sync_dir(target.parent);sync_dir(backup.parent)
                    switch_target(target,self._files(candidate_state,candidate))
                    self._check_link(candidate_state,[candidate]);self._verify(candidate_state,candidate)
                    final=copy.deepcopy(candidate_state);final.update(current=candidate,previous=state['current'],transaction=None)
                    self._save(final,'upgrade' if upgrade else 'install')
                    if identity is not None:
                        _,_,backup=metadata_paths(target)
                        try:backup.rmdir();sync_dir(backup.parent)
                        except OSError:pass
                    return final
                except (ValueError,OSError,RuntimeError):
                    try:self._recover(candidate_state)
                    except (ValueError,OSError,RuntimeError):pass # Authoritative pending is retained for explicit recovery.
                    raise

    def rollback(self,app,env):
        with target_lock(None):
            home=self._home(app,env)
            if not home.is_dir():raise ValueError('application is not installed')
            with service_lock(home/'deploy.lock'):
                state=self._state(app,env)
                if state['transaction']:return self._recover(state)
                if state['previous'] is None:raise ValueError('no previous static release available')
                self._verify(state,state['current']);self._verify(state,state['previous']);self._check_link(state,[state['current']])
                target=Path(state['target_dir']);pending=copy.deepcopy(state)
                pending['transaction']={'from':state['current'],'to':state['previous'],'phase':'rollback','empty_identity':None}
                self._save(pending,'rollback-prepared')
                try:
                    switch_target(target,self._files(state,state['previous']));self._check_link(state,[state['previous']])
                    final=copy.deepcopy(state);final.update(current=state['previous'],previous=state['current'],transaction=None)
                    self._save(final,'rollback');return final
                except (ValueError,OSError,RuntimeError):
                    try:self._recover(pending)
                    except (ValueError,OSError,RuntimeError):pass
                    raise

    def operate(self,app,env,operation,tail=100):
        if operation not in ('status','logs'):raise ValueError(f'{operation} does not apply to static files; manage the web server separately')
        state=self._state(app,env);home=self._home(app,env)
        if operation=='logs':
            path=home/'events.jsonl';reject_links(path)
            if not path.exists():return ''
            from collections import deque
            with path.open(encoding='utf-8') as source:return ''.join(deque(source,maxlen=tail))
        if state['current']:
            self._verify(state,state['current']);refs=[state['current']]
            if state['transaction']:refs.append(state['transaction']['to'])
            self._check_link(state,refs)
        return json.dumps({'deployment_type':'static','application':app,'environment':env,'version':state['current']['version'] if state['current'] else None,'target_dir':state['target_dir'],'pending':state['transaction'] is not None},ensure_ascii=False)
