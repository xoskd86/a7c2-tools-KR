"""Resolve or prepare the official ExifTool helper without camera access."""
import io
import filecmp
from pathlib import Path,PurePosixPath
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
import uuid
from .paths import ROOT,data_root

VERSION='13.59'


def executable(folder,platform=None):
    return Path(folder)/('exiftool.exe' if (platform or sys.platform)=='win32' else 'exiftool')


def download(platform):
    name=('exiftool-'+VERSION+'_64.zip' if platform=='win32' else 'Image-ExifTool-'+VERSION+'.tar.gz')
    urls=['https://downloads.sourceforge.net/project/exiftool/'+name,'https://exiftool.org/'+name]
    errors=[]
    for url in urls:
        print('공식 ExifTool 다운로드: '+url,flush=True)
        try:
            request=urllib.request.Request(url,headers={'User-Agent':'A7C2Tools'})
            with urllib.request.urlopen(request,timeout=30) as response:data=response.read()
            if not data.startswith(b'PK' if platform=='win32' else b'\x1f\x8b'):
                raise ValueError('Server did not return an ExifTool archive')
            return data,url
        except Exception as exc:
            detail=url+': '+repr(exc);errors.append(detail);print(detail,flush=True)
    raise RuntimeError('ExifTool 자동 다운로드에 실패해 이번에는 카메라 작업을 시작하지 않았습니다. 네트워크를 확인한 뒤 같은 버튼을 다시 누르세요. 전체 오류:\n'+'\n'.join(errors))


def relative_name(name):
    path=PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts:raise ValueError('Unexpected ExifTool archive path: '+name)
    parts=path.parts
    if parts and (parts[0].startswith('exiftool-') or parts[0].startswith('Image-ExifTool-')):parts=parts[1:]
    return Path(*parts) if parts else None


def unpack(data,destination,platform):
    destination=Path(destination)
    if platform=='win32':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.infolist():
                if member.is_dir():continue
                relative=relative_name(member.filename)
                if relative is None:continue
                if relative.name=='exiftool(-k).exe':relative=relative.with_name('exiftool.exe')
                target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(archive.read(member))
        if not (destination/'exiftool_files').is_dir():raise ValueError('ExifTool archive lacks its companion exiftool_files directory')
    else:
        with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
            for member in archive.getmembers():
                if not member.isfile():continue
                relative=relative_name(member.name)
                if relative is None or (relative.parts[0]!='lib' and str(relative) not in ('exiftool','LICENSE','README','Changes')):continue
                target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
        if not (destination/'lib/Image/ExifTool.pm').is_file():raise ValueError('ExifTool archive lacks its Perl modules')
        executable(destination,platform).chmod(0o755)
    result=executable(destination,platform)
    if not result.is_file():raise ValueError('ExifTool executable was not found in its archive')
    return result


def install(destination,*,platform=None,archive=None):
    platform=platform or sys.platform
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    data,source=(Path(archive).read_bytes(),'Build-supplied archive: '+Path(archive).name) if archive else download(platform)
    with tempfile.TemporaryDirectory(prefix='a7c2-exiftool-',dir=destination.parent) as folder:
        tool=unpack(data,folder,platform)
        result=subprocess.run([str(tool),'-ver'],capture_output=True,text=True,timeout=30,
                              creationflags=0x08000000 if os.name=='nt' else 0)
        if result.returncode:raise RuntimeError('ExifTool 실행 실패: '+result.stdout+'\n'+result.stderr)
        shutil.copytree(folder,destination,dirs_exist_ok=True)
    (destination/'SOURCE.txt').write_text('ExifTool '+VERSION+' by Phil Harvey\n'+source+'\nOriginal license retained in LICENSE / exiftool_files.\n',encoding='utf-8')
    (destination/'installed.txt').write_text(result.stdout.strip(),encoding='utf-8')
    print('ExifTool 준비 완료: '+result.stdout.strip(),flush=True)
    return executable(destination,platform)


def ensure(preferred=None):
    cache=data_root()/('helpers/exiftool-'+VERSION+'-'+sys.platform)
    candidates=[Path(preferred)] if preferred else []
    candidates+=[executable(ROOT/'desktop-assets/exiftool')]
    if (cache/'installed.txt').is_file():candidates.append(executable(cache))
    found=shutil.which('exiftool')
    if found:candidates.append(Path(found))
    for path in candidates:
        if path.is_file():return str(path.resolve())
    print('Lightroom용 ExifTool 보조 프로그램을 준비하는 중입니다(컴퓨터 쪽만, 카메라에 연결하지 않음).',flush=True)
    return str(install(cache).resolve())


def persist_windows(tool):
    """Reuse identical helpers; publish updates without replacing running files."""
    source=Path(tool).resolve()
    helpers=data_root()/'helpers';helpers.mkdir(parents=True,exist_ok=True)
    def payload(folder):
        result={'exiftool.exe':folder/'exiftool.exe'}
        companion=folder/'exiftool_files'
        if companion.is_dir():
            result.update({str(p.relative_to(folder)):p for p in companion.rglob('*') if p.is_file()})
        return result
    original=payload(source.parent);original['exiftool.exe']=source
    candidates=[helpers/'exiftool',*sorted(helpers.glob('exiftool-local-*'),reverse=True)]
    for folder in candidates:
        try:
            existing=payload(folder)
            if set(existing)==set(original) and all(filecmp.cmp(original[n],existing[n],shallow=False) for n in original):
                return str((folder/'exiftool.exe').resolve())
        except OSError:
            # A protected/running older copy is left in place.
            continue
    destination=helpers/('exiftool-local-'+uuid.uuid4().hex[:12])
    with tempfile.TemporaryDirectory(prefix='.exiftool-stage-',dir=helpers) as staging:
        stage=Path(staging)
        shutil.copy2(source,stage/'exiftool.exe')
        if (source.parent/'exiftool_files').is_dir():
            shutil.copytree(source.parent/'exiftool_files',stage/'exiftool_files')
        for name in ('SOURCE.txt','installed.txt'):
            if (source.parent/name).is_file():shutil.copy2(source.parent/name,stage/name)
        stage.replace(destination)
    return str((destination/'exiftool.exe').resolve())
