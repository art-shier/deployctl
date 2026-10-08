"""Private local platform identity. Never copied into application snapshots."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import tempfile

from .runtime_snapshot import reject_links

LEGACY_PATH = '/etc/deployctl/client.json'
DEFAULT_SERVER = 'https://ctl.shier.art'


def default_path():
    return Path.home() / '.ctl' / 'client.json'


@dataclass
class Credentials:
    server: str
    token: str

    @classmethod
    def load(cls, path=None):
        value = cls._read(path)
        if value is None or 'token' not in value:
            raise ValueError('not logged in; run ctl login')
        return cls(value['server'], value['token'])

    @classmethod
    def get_server(cls, path=None):
        value = cls._read(path)
        return value['server'] if value is not None else DEFAULT_SERVER

    @classmethod
    def set_server(cls, path, server):
        from .platform_client import validate_origin
        server = validate_origin(server if '://' in server else 'https://' + server)
        previous = cls._read(path)
        value = previous if previous is not None and previous['server'] == server else {'server': server}
        cls._write(path, value)
        return server, previous is not None and 'token' in previous and previous['server'] != server

    @classmethod
    def _read(cls, path):
        if path is None:
            path = default_path()
            cls._migrate_legacy(path)
        path = Path(path).expanduser().absolute()
        reject_links(path)
        if not path.exists():
            return None
        for item in (path, path.parent):
            info = item.stat()
            if os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise ValueError('platform credentials require current ownership and private permissions')
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0)
        with os.fdopen(os.open(path, flags), 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
                raise ValueError('platform credentials must be a bounded regular file')
            try: value = json.loads(handle.read(16385))
            except (ValueError, UnicodeError): raise ValueError('invalid platform credentials') from None
        if not isinstance(value, dict) or set(value) not in ({'server'}, {'server','token'}):
            raise ValueError('invalid platform credentials')
        from .platform_client import validate_origin, validate_token
        value['server'] = validate_origin(value['server'])
        if 'token' in value:
            value['token'] = validate_token(value['token'])
        return value

    @classmethod
    def _migrate_legacy(cls, target):
        reject_links(target)
        if target.exists():
            return
        legacy = Path(LEGACY_PATH).absolute()
        # A system credential owned by someone else is not this user's login.
        # Import only files that already satisfy the previous privacy contract.
        try:
            reject_links(legacy)
            info = legacy.stat()
            parent = legacy.parent.stat()
        except (OSError, ValueError):
            return
        if not stat.S_ISREG(info.st_mode):
            return
        if os.name != 'nt' and any(item.st_uid != os.getuid() or item.st_mode & 0o077 for item in (info, parent)):
            return
        try:
            value = cls._read(legacy)
        except OSError:
            return
        if value is not None:
            cls._write(target, value, replace=False)

    @classmethod
    def save(cls, path, server, token):
        from .platform_client import validate_origin, validate_token
        value = cls(validate_origin(server), validate_token(token))
        cls._write(path, value.__dict__)
        return value

    @classmethod
    def _write(cls, path, value, replace=True):
        path = (default_path() if path is None else Path(path).expanduser()).absolute()
        reject_links(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != 'nt':
            info = path.parent.stat()
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('credential directory requires current ownership and permissions 700')
        fd, name = tempfile.mkstemp(dir=path.parent, prefix='.client-')
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as handle:
                json.dump(value,handle); handle.flush(); os.fsync(handle.fileno())
            os.chmod(name,0o600)
            if replace:
                os.replace(name,path)
            else:
                try:
                    os.link(name,path)
                except FileExistsError:
                    pass  # A simultaneous login/configuration takes priority over migration.
        finally:
            if os.path.exists(name): os.unlink(name)


def read_token_file(path):
    path = Path(path).absolute()
    reject_links(path)
    flags = os.O_RDONLY | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_NONBLOCK',0) | getattr(os,'O_BINARY',0)
    with os.fdopen(os.open(path,flags),'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096 or (os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077)):
            raise ValueError('token file requires current ownership, regular file and permissions 600')
        from .platform_client import validate_token
        return validate_token(handle.read(4097).decode('ascii').strip())


def read_registry_token_file(path):
    path=Path(path).absolute();reject_links(path)
    flags=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0)
    with os.fdopen(os.open(path,flags),'rb') as handle:
        info=os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>12290 or (os.name!='nt' and (info.st_uid!=os.getuid() or info.st_mode&0o077)):
            raise ValueError('registry verification file requires current ownership and permissions 600')
        from .platform_client import validate_registry_token
        try:return validate_registry_token(handle.read(12291).decode('ascii').strip())
        except UnicodeError:raise ValueError('invalid external registry verification token') from None
