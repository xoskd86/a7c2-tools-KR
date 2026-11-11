"""Account for a zero-commit name-space failure before a fresh installation.

No native execution, firmware/settings/resource writes or old-job replay.
Remove only the three exact completed temporary artifacts after current-byte
and resource-binding checks. The failed job remains immutable evidence.
"""
import json
from contextlib import contextmanager
from pathlib import Path
import re
import struct
from .paths import write_json


@contextmanager
def session(factory,identity):
    with factory(identity,allow_service_resume=True) as camera:
        try:yield camera
        except BaseException:
            mark=getattr(camera,'mark_unknown',None)
            if mark:mark()
            raise


def source(folder):
    from looktool import nameworker
    root=Path(folder).resolve()
    state=json.loads((root/'direct.private.json').read_text(encoding='utf-8'))
    build=state.get('forward_name_build')
    if state.get('phase')!='NEEDS_REVIEW' or not build or state.get('inverse_name_build'):
        raise ValueError('Not a completed pre-program name failure')
    journal=state.get('journal',[])
    if not journal or journal[-1].get('event')!='native_intent' or journal[-1].get('result')!='a7c2.names.result' or journal[-1].get('mode')!=1:
        raise ValueError('Unknown last operation; do not replay')
    if any(r.get('event')=='native_intent' and (r.get('result')=='checkpoint.program.result' and r.get('mode')==1
            or r.get('result')=='a7c2.wbi.result') for r in journal):
        raise ValueError('Programs/reload may have changed; not a pre-program failure')
    reports=list((root/'evidence').glob('*-names-forward-1.bin'))
    if len(reports)!=1:raise ValueError('One complete failed name report is required')
    raw=reports[0].read_bytes()
    if len(raw)!=nameworker.REPORT.size:raise ValueError('Incomplete name result')
    v=nameworker.REPORT.unpack(raw)
    if (v[:3]!=(b'A7NMR001',1,1) or v[3] not in (2,3) or v[4]!=build['count'] or v[5]!=-28
        or not 1<=v[4]<=68 or not 0<=v[12]<=v[4] or v[11]<=0
        or any(v[i] for i in (6,7,8,9,10,13,14,15,16,17))):
        raise ValueError('Names are not proved uncommitted/clean; preserve evidence')
    if set(s['file'] for s in build['files'])!=set(nameworker.filenames()):
        raise ValueError('Resource inventory differs')
    return root,state,raw


def run(folder, job, *, factory=None):
    from looktool import resources,checkpoint,nameworker
    from looktool.checkpointjob import CheckpointCamera
    root,state,raw=source(folder)
    build=state['forward_name_build']
    with session(factory or CheckpointCamera,state['identity']) as camera:
        print('완료된 이름 실패 보고서, 원래 프로그램 영역과 이름 바인딩을 확인합니다. 펌웨어나 설정에는 쓰지 않습니다.',flush=True)
        images={name:camera.read(path) for name,path in checkpoint.PROGRAM_PATHS.items()}
        for item in state['program_builds']:
            path=Path(item['path'])
            if path.parent.resolve()!=(root/'worker').resolve():raise ValueError('Unexpected program evidence path')
            text=path.with_suffix('.private.h').read_text(encoding='ascii')
            arrays={int(i):bytes(map(int,values.split(','))) for i,values in
                re.findall(r'static const u8 old_(\d+)\[\]=\{([\d,]+)\};',text)}
            rows=re.findall(r'\{(\d+)UL,(\d+),([01]),old_(\d+),new_(\d+)\}',text)
            if len(rows)!=item['windows']:raise ValueError('Incomplete original program windows')
            for at,size,chip,old,new in rows:
                expected=arrays[int(old)]
                if len(expected)!=int(size) or images['cpapp-b.bin' if chip=='1' else 'appFw.so'][int(at):int(at)+int(size)]!=expected:
                    raise ValueError('Current program windows differ; do not replay')
        for spec in build['files']:
            data=camera.read('/usr/share/app/'+spec['file']);parsed=resources.decode(data);plan=spec['inverse']
            if len(data)!=spec['before_bytes'] or parsed['count']!=plan['before_count']:
                raise ValueError('Current name resource differs: '+spec['file'])
            for change in plan['changes']:
                if change['index_offset']<16+2*parsed['count'] and struct.unpack_from('<H',data,change['index_offset'])[0]!=change['before_pointer']:
                    raise ValueError('Current name pointer differs: '+spec['file'])
        expected={'lut-names-staged.so':Path(build['path']).read_bytes(),
            'a7c2.names.request':struct.pack('<8sII',b'A7NAM001',1,0),'a7c2.names.result':raw}
        for name in ('libJit.so','checkpoint.staged.so','checkpoint.program.request','checkpoint.program.result',
                     'checkpoint.names.request','checkpoint.names.result','a7c2.wbi.request','a7c2.wbi.result'):
            if camera.temp_read(name,True) is not None:raise ValueError('Unaccounted temporary state: '+name)
        for name,data in expected.items():
            value=camera.temp_read(name,True)
            if value is not None and value!=data:raise ValueError('Unexpected temporary bytes: '+name)
        # Result last: interruption must retain the accounting report.
        for name in expected:
            if camera.temp_read(name,True) is not None:camera.temp_remove(name)
            if camera.temp_read(name,True) is not None:raise ValueError('Temporary cleanup incomplete: '+name)
    result=dict(failed_workspace=str(root),pre_program_failure_accounted=True,firmware_written=False,
        settings_written=False,name_resources_written=False,temporary_artifacts_cleaned=True)
    write_json(Path(job)/'name-failure-review.private.json',result)
    print('이전에 이름이나 프로그램을 기록하지 않았음을 확인했습니다. 해당 임시 보고서／worker만 정리했습니다. 원래 실패 기록은 보존되며, 다음에는 새 트랜잭션을 사용합니다.',flush=True)
    return result
