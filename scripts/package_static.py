"""Create a reproducible static archive from a build output directory."""
import argparse
import gzip
import os
from pathlib import Path
import shutil
import stat
import sys
import tarfile
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployctl import static_archive as contract
from deployctl.runtime_snapshot import reject_links


class BoundedWriter:
    def __init__(self, file): self.file=file
    def write(self, data):
        if self.file.tell()+len(data)>contract.MAX_PACKAGE:
            raise ValueError('static package exceeds compressed size limit')
        return self.file.write(data)
    def tell(self): return self.file.tell()
    def flush(self): return self.file.flush()


def package_static(directory: Path, output: Path, archive_format='tar.gz') -> contract.ArchiveInfo:
    directory=Path(directory).absolute();output=Path(output).absolute()
    reject_links(directory);reject_links(output)
    if not directory.is_dir():raise ValueError('build output must be a directory')
    directory=directory.resolve();output=output.resolve()
    if output.is_relative_to(directory):raise ValueError('archive output cannot be inside build output')
    if archive_format not in ('zip','tar.gz'):raise ValueError('static archive format must be zip or tar.gz')
    entries=[];total=0;files=0
    for path in directory.rglob('*'):
        info=path.lstat();is_dir=stat.S_ISDIR(info.st_mode)
        if not is_dir and not stat.S_ISREG(info.st_mode):raise ValueError('static build output contains a link or special file')
        name=contract.archive_name(path.relative_to(directory).as_posix(),is_dir)
        entries.append((name,path,is_dir,info.st_size))
        if len(entries)>contract.MAX_ENTRIES:raise ValueError('too many static entries')
        if not is_dir:
            files+=1;total+=info.st_size
            if info.st_size>contract.MAX_FILE or total>contract.MAX_EXPANDED:raise ValueError('static build output exceeds size limit')
    if not files:raise ValueError('static build output has no files')
    entries.sort(key=lambda item:item[0].encode('utf-8'))
    output.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.ctl-package-',dir=output.parent);temporary=Path(name)
    try:
        with os.fdopen(fd,'w+b') as raw:
            bounded=BoundedWriter(raw)
            if archive_format=='tar.gz':
                with gzip.GzipFile(filename='',mode='wb',fileobj=bounded,mtime=0,compresslevel=9) as gz:
                    with tarfile.open(fileobj=gz,mode='w|',format=tarfile.PAX_FORMAT) as archive:
                        for name,path,is_dir,size in entries:
                            item=tarfile.TarInfo(name);item.mtime=0;item.mode=0o755 if is_dir else 0o644
                            item.type=tarfile.DIRTYPE if is_dir else tarfile.REGTYPE;item.size=0 if is_dir else size
                            if is_dir:archive.addfile(item)
                            else:
                                with open_file(path,size) as source:archive.addfile(item,source)
            else:
                with zipfile.ZipFile(bounded,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
                    for name,path,is_dir,size in entries:
                        item=zipfile.ZipInfo(name+('/' if is_dir else ''),(1980,1,1,0,0,0));item.create_system=3
                        item.external_attr=((stat.S_IFDIR|0o755) if is_dir else (stat.S_IFREG|0o644))<<16
                        item.compress_type=zipfile.ZIP_DEFLATED
                        if is_dir:archive.writestr(item,b'')
                        else:
                            with open_file(path,size) as source,archive.open(item,'w') as dest:shutil.copyfileobj(source,dest,65536)
            raw.flush();os.fsync(raw.fileno())
        result=contract.inspect_static_archive(temporary)
        os.replace(temporary,output)
        output.with_name(output.name+'.sha256').write_text(f'{result.sha256}  {output.name}\n',encoding='ascii')
        return result
    finally:
        temporary.unlink(missing_ok=True)


def open_file(path,size):
    reject_links(path)
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0))
    info=os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size!=size:
        os.close(fd);raise ValueError('static build file changed during packaging')
    return os.fdopen(fd,'rb')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--archive-format',choices=('zip','tar.gz'),default='tar.gz');args=parser.parse_args()
    try:info=package_static(args.directory,args.output,args.archive_format)
    except (ValueError,OSError) as error:parser.error(str(error))
    print(f'{info.sha256}  {args.output}')

if __name__=='__main__':main()
