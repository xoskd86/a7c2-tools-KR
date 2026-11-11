"""Fresh sound requests supersede history; one live session owns device work."""
from contextlib import contextmanager
from functools import wraps
import json
from pathlib import Path
import threading
import uuid

_local=threading.local()


@contextmanager
def command_lock(root):
    from looktool.devicelock import DeviceLock
    root=Path(root).resolve()
    held=getattr(_local,'locks',set())
    if root in held:
        yield
        return
    lock=DeviceLock(root.parent/('.'+root.name+'.sound.lock'))
    try:lock.__enter__()
    except ValueError as exc:
        raise RuntimeError('다른 사운드 작업이 아직 실행 중입니다. 끝난 뒤 다시 누르세요.') from exc
    _local.locks=held|{root}
    try:yield
    finally:
        _local.locks=held
        lock.__exit__(None,None,None)


def active():
    return getattr(_local,'replacement',None)


def optional_job(path):
    if not path:return None
    try:return json.loads((Path(path)/'job.private.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):return None


def replace_request(command,*,refresh=True):
    """Old task records never block a new sound request.

    refresh=True re-reads the connected body for the current selection
    (Save, Audition, On). refresh=False keeps the recorded switch/installation
    jobs that the command itself acts on (Off, Restore, Verify). Both archive
    an unresolved earlier operation and reconcile only accounted camera files.
    """
    def decorate(fn):
        @wraps(fn)
        def run(root,state,*args,**kwargs):
            if active() is not None:
                # Save prepares support with On inside the same request/session.
                return fn(root,state,*args,**kwargs)
            with command_lock(root):
                with CurrentRequest(root,state,command,refresh=refresh):
                    return fn(root,state,*args,**kwargs)
        return run
    return decorate


def supersede_pending(root,state,command):
    """Host-only records: archive an unresolved operation instead of blocking."""
    import sound_cli
    pending=state.get('pending')
    if not pending:return False
    state.setdefault('history',[]).append(dict(command=pending.get('command'),job=pending.get('job'),
        phase='superseded_by_current_selection',outcome=pending.get('outcome'),replacement=command))
    state['pending']=None
    sound_cli.save(root,state)
    return True


class BorrowedCamera:
    def __init__(self,request,expected):self.request,self.expected=request,expected
    def __enter__(self):return self.request.open(self.expected)
    def __exit__(self,*args):return False


class CurrentRequest:
    def __init__(self,root,state,command,*,refresh=True):
        self.root,self.state,self.command,self.refresh=Path(root),state,command,refresh
        self.camera=None;self.opening=False;self.previous=None

    def __enter__(self):
        import sound_cli
        self.previous=active()
        if self.previous:raise RuntimeError('Nested sound replacement request')
        self.evidence=self.root/'jobs'/('replace-'+uuid.uuid4().hex[:12])
        self.evidence.mkdir(parents=True)
        (self.evidence/'workspace-before.private.json').write_text(json.dumps(self.state,indent=2))
        pending=self.state.get('pending')
        if pending:
            self.state.setdefault('history',[]).append(dict(command=pending.get('command'),job=pending.get('job'),
                phase='superseded_by_current_selection',outcome=pending.get('outcome'),replacement=self.command))
            self.state['pending']=None
            print('이번 선택으로 사운드를 교체합니다. 이전 작업은 보관 처리했으며, 이어서 카메라의 현재 상태를 읽고 이전 명령은 재실행하지 않습니다.',flush=True)
        if self.refresh:
            if self.state.get('native_job'):
                self.state.setdefault('history',[]).append(dict(command='previous_native_support',
                    job=self.state.pop('native_job'),phase='refresh_from_current_camera'))
            self.state.pop('native_identity',None)
        sound_cli.save(self.root,self.state)
        _local.replacement=self
        return self

    def __exit__(self,*args):
        try:
            if self.camera:self.camera.__exit__(*args)
        finally:_local.replacement=self.previous
        return False

    def open(self,expected):
        import trial
        if self.camera is None:
            self.opening=True
            try:
                camera=trial.factory(expected)
                self.camera=camera.__enter__()
            finally:self.opening=False
            self.prepare()
        if expected and any(expected.get(k)!=self.camera.identity.get(k) for k in ('model','serial_sha256')):
            raise ValueError('현재 사운드 작업의 카메라 본체 바인딩이 일치하지 않습니다')
        return self.camera

    def prepare(self):
        prepare_current(self.camera,self.evidence,self.command)
        self.state.pop('replacement_stop',None)
        import sound_cli
        sound_cli.save(self.root,self.state)


def prepare_current(camera,folder,command):
    """Replace inert sound staging over current bytes, with no old-job gate."""
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    # Keep the OS/process mutex and the actual loader occupancy check.
    # Never run a second native plugin over an occupied execution entry.
    if camera.temp_read('libJit.so',True) is not None:
        camera.mark_unknown()
        link=getattr(camera,'link',None)
        if link is not None:link.mutating=True
        raise RuntimeError('카메라 실행 진입점이 아직 사용 중이라 이번 사운드 작업은 시작되지 않았습니다. 실행 중인 작업이 끝날 때까지 기다리세요.')
    names=('shutter.staged.so','shutter.probe.result','a7c2.probe.result',
           'lut-runtime-staged.so','a7c2.runtime.result','lut-program-staged.so',
           'a7c2.install.request','a7c2.install.result','a7c2.wbi.request','a7c2.wbi.result',
           'checkpoint.staged.so','checkpoint.program.request','checkpoint.program.result',
           'checkpoint.names.request','checkpoint.names.result')
    actual={n:camera.read('/tmp/jiritsu/'+n,True) for n in names}
    actual={n:raw for n,raw in actual.items() if raw is not None}
    # Save never needs the old RAM audition marker. A new audition removes
    # that fixed temporary marker and registers the CURRENT selected bytes;
    # it does not release any old CP handle or replay a previous worker.
    if command=='audition':
        marker=camera.read('/tmp/jiritsu/shutter.custom.lease',True)
        if marker is not None:actual['shutter.custom.lease']=marker
    for name,raw in actual.items():
        (folder/(name+'.before.private.bin')).write_bytes(raw)
    try:
        if camera.temp_read('libJit.so',True) is not None:
            raise RuntimeError('카메라가 다른 작업을 시작해 이번 사운드 교체는 실행되지 않았습니다.')
        for name,raw in actual.items():
            current=camera.read('/tmp/jiritsu/'+name,True)
            if current!=raw:raise RuntimeError('카메라 임시 상태가 계속 바뀌고 있습니다. 현재 작업이 끝날 때까지 기다리세요.')
        for name in actual:
            camera.temp_remove(name)
            current=camera.read('/tmp/jiritsu/'+name,True)
            if current is not None:raise RuntimeError('현재 사운드 임시 파일 교체가 완료되지 않았습니다: '+name)
    except BaseException:
        camera.mark_unknown()
        raise
