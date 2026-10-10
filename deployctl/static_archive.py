"""Bounded static ZIP/tar.gz inspection and safe file extraction."""
from dataclasses import dataclass
import gzip
import hashlib
import os
from pathlib import Path
import re
import stat
import tarfile
import zipfile

from .runtime_snapshot import reject_links

MAX_PACKAGE = 256 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024
MAX_FILE = 256 * 1024 * 1024
MAX_ENTRIES = 50_000
MAX_PATH = 4096
CHUNK = 64 * 1024


@dataclass(frozen=True)
class ArchiveInfo:
    archive_format: str
    sha256: str
    size: int
    expanded_size: int
    entry_count: int


def archive_name(value, directory):
    if not isinstance(value, str) or '\\' in value or value.startswith('/') or re.match(r'^[A-Za-z]:',value):
        raise ValueError('unsafe static archive path')
    if any(ord(c)<32 or 127<=ord(c)<=159 for c in value): raise ValueError('invalid static path characters')
    try: size=len(value.encode('utf-8'))
    except UnicodeError: raise ValueError('invalid static path encoding') from None
    if size>MAX_PATH: raise ValueError('static archive path exceeds limit')
    if not directory and value.endswith('/'): raise ValueError('file has a directory path')
    parts=value.rstrip('/').split('/')
    if '..' in parts: raise ValueError('static archive path escapes root')
    parts=[p for p in parts if p not in ('','.')]
    if not parts: raise ValueError('empty static archive path')
    return '/'.join(parts)


class _Entries:
    def __init__(self,destination):
        self.destination=destination
        self.seen={};self.parents=set();self.nodes=set();self.total=0;self.count=0;self.files=0

    def consume(self,name,directory,size,reader=None):
        name=archive_name(name,directory)
        self.count+=1
        if self.count>MAX_ENTRIES or name in self.seen: raise ValueError('duplicate or excessive static entries')
        parts=name.split('/')
        parents=['/'.join(parts[:i]) for i in range(1,len(parts))]
        if any(self.seen.get(p) is False for p in parents) or not directory and name in self.parents:
            raise ValueError('static file/directory prefix collision')
        self.parents.update(parents);self.seen[name]=directory
        self.nodes.update(parents);self.nodes.add(name)
        if len(self.nodes)>MAX_ENTRIES:raise ValueError('too many expanded static entries')
        if directory:
            if size: raise ValueError('directory contains payload')
            if self.destination is not None:
                target=self.destination/name;target.mkdir(parents=True,exist_ok=True,mode=0o755)
                target.chmod(0o755)
            return
        if size<0 or size>MAX_FILE or self.total+size>MAX_EXPANDED: raise ValueError('static expansion exceeds limit')
        self.files+=1
        output=None
        try:
            if self.destination is not None:
                target=self.destination/name
                target.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
                reject_links(target)
                output=target.open('xb');target.chmod(0o644)
            actual=0
            while True:
                data=reader.read(CHUNK)
                if not data: break
                actual+=len(data);self.total+=len(data)
                if actual>size or actual>MAX_FILE or self.total>MAX_EXPANDED: raise ValueError('static expansion exceeds limit')
                if output is not None: output.write(data)
            if actual!=size: raise ValueError('truncated static file')
        finally:
            if output is not None: output.close()


class _BoundedGzip:
    def __init__(self,reader):
        self.reader=reader;self.total=0
    def read(self,size=-1):
        data=self.reader.read(CHUNK if size<0 else min(size,CHUNK))
        self.total+=len(data)
        if self.total>MAX_EXPANDED+MAX_ENTRIES*8192+1024*1024: raise ValueError('static tar stream exceeds limit')
        return data


class _SafeTarInfo(tarfile.TarInfo):
    def _proc_member(self,archive):
        # tarfile buffers PAX/GNU metadata internally before returning members.
        # Bound those allocations and reject sparse processing before it starts.
        if self.type==tarfile.GNUTYPE_SPARSE:raise ValueError('static sparse entries are forbidden')
        if self.type in (tarfile.XHDTYPE,tarfile.XGLTYPE,tarfile.GNUTYPE_LONGNAME,tarfile.GNUTYPE_LONGLINK) and self.size>1024*1024:
            raise ValueError('static TAR metadata exceeds limit')
        return super()._proc_member(archive)


def _process(package,destination):
    package=Path(package)
    if package.is_symlink() or not package.is_file(): raise ValueError('static package must be a regular file')
    size=package.stat().st_size
    if not 0<size<=MAX_PACKAGE: raise ValueError('static package exceeds size limit')
    checksum=hashlib.sha256()
    with package.open('rb') as raw:
        magic=raw.read(4);raw.seek(0)
        for block in iter(lambda:raw.read(CHUNK),b''): checksum.update(block)
    entries=_Entries(destination)
    try:
        if magic.startswith(b'PK'):
            format_name='zip'
            with zipfile.ZipFile(package) as archive:
                if len(archive.infolist())>MAX_ENTRIES: raise ValueError('too many static entries')
                for item in archive.infolist():
                    mode=item.external_attr>>16 if item.create_system==3 else 0
                    kind=stat.S_IFMT(mode)
                    directory=item.is_dir() or kind==stat.S_IFDIR or bool(item.external_attr&0x10)
                    if item.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):raise ValueError('unsupported static ZIP compression')
                    if not item.flag_bits&0x800 and not item.orig_filename.isascii():raise ValueError('static ZIP names must use UTF-8')
                    if item.flag_bits&1 or kind not in (0,stat.S_IFREG,stat.S_IFDIR):
                        raise ValueError('static archive links or special files are forbidden')
                    if directory: entries.consume(item.orig_filename,True,item.file_size)
                    else:
                        with archive.open(item) as reader:entries.consume(item.orig_filename,False,item.file_size,reader)
        elif magic.startswith(b'\x1f\x8b'):
            format_name='tar.gz'
            with gzip.open(package,'rb') as gz:
                bounded=_BoundedGzip(gz)
                with tarfile.open(fileobj=bounded,mode='r|',tarinfo=_SafeTarInfo) as archive:
                    for item in archive:
                        if not (item.isdir() or item.isfile()) or item.issparse() or any(key.startswith('GNU.sparse') for key in item.pax_headers): raise ValueError('static archive contains a non-regular entry')
                        if item.isdir(): entries.consume(item.name,True,item.size)
                        else:
                            with archive.extractfile(item) as reader: entries.consume(item.name,False,item.size,reader)
                    while True:
                        trailer=archive.fileobj.read(CHUNK)
                        if not trailer: break
                        if any(trailer): raise ValueError('static TAR has trailing payload')
                # Validate gzip checksum/footer even after TAR end markers.
                while True:
                    trailer=bounded.read(CHUNK)
                    if not trailer: break
                    if any(trailer): raise ValueError('static TAR has trailing payload')
        else: raise ValueError('static package must be ZIP or tar.gz')
    except (OSError,EOFError,UnicodeError,tarfile.TarError,zipfile.BadZipFile,RuntimeError,NotImplementedError) as exc:
        raise ValueError('invalid or damaged static archive') from exc
    if not entries.files: raise ValueError('static archive has no regular files')
    if destination is not None:
        destination.chmod(0o755)
        for parent,dirs,_ in os.walk(destination):
            for name in dirs: (Path(parent)/name).chmod(0o755)
    return ArchiveInfo(format_name,checksum.hexdigest(),size,entries.total,entries.count)


def inspect_static_archive(package):
    return _process(package,None)


def extract_static_archive(package,destination):
    destination=Path(destination)
    reject_links(destination)
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError('static extraction destination must be empty')
    destination.mkdir(parents=True,exist_ok=True,mode=0o755)
    return _process(package,destination)
