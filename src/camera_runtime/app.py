"""A7C II 2.01 preview installer. Per-body backup, explicit phases, no retries."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import struct
import core

def event(message):print(message,flush=True)

class Job:
    def __init__(self,workspace):
        self.root=Path(workspace).resolve();self.state_path=self.root/'state.json'
    def save(self,phase,**values):
        self.state.update(values);self.state['phase']=phase
        core.save_json(self.state_path,self.state)
    def audit(self,step,**values):
        row={'time':datetime.now().isoformat(),'step':step,**values}
        self.state.setdefault('journal',[]).append(row);core.save_json(self.state_path,self.state)
    def load(self):
        core.assets();self.state=core.read_json(self.state_path)
        if self.state.get('format')!=1 or self.state.get('model')!=core.MODEL:raise ValueError('유효한 카메라 작업 폴더가 아닙니다')
        for name in self.state['backup_files']:
            if (self.root/'original'/name).read_bytes()!=(Path(self.state['backup_copy'])/name).read_bytes():
                raise ValueError('두 백업의 내용이 다릅니다: '+name)
        core.backup_records((self.root/'original/backup.private.bin').read_bytes())
        if 'compatibility' in self.state:
            import compat
            compat.validate(self.root,self.state)
        return self
    def module(self,session,name,mode=None,byte_build=None):
        blob=(core.BASE/'assets'/f'{name}.so').read_bytes()
        dynamic=name=='installer' and 'compatibility' in self.state
        if dynamic:
            import compat
            compat.validate(self.root,self.state)
            if self.state['compatibility']['already_enabled']:raise ValueError('이미 활성화된 상태에서는 설치기를 실행하면 안 됩니다')
            if 'byte_builds' in self.state['compatibility']:
                if byte_build is None:
                    return [self.module(session,name,mode,build) for build in self.state['compatibility']['byte_builds']]
                blob=Path(byte_build['path']).read_bytes()
            else:blob=(self.root/'compat-worker/program.so').read_bytes()
        result={'installer':'a7c2.install.result','probe':'a7c2.probe.result',
                'wbi':'a7c2.wbi.result','runtime':'a7c2.runtime.result'}[name]
        request={'installer':('a7c2.install.request',b'A7INS001'),
                 'wbi':('a7c2.wbi.request',b'A7WBI001')}.get(name)
        names=['libJit.so','a7c2-preview-staged.so',result]
        if name=='runtime':names.append('a7c2.probe.result')
        if request:names.append(request[0])
        for item in names:
            if session.read('/tmp/jiritsu/'+item,True) is not None:
                raise RuntimeError('이전 임시 파일이 남아 있습니다. 먼저 보존하고 결과를 확인하세요. 바로 재시도하지 마세요: '+item)
        if session.read('/tmp/jiritsu/../../etc/ld.so.preload',True):raise RuntimeError('기존 사전 로드 구성이 발견되어 중지합니다')
        created=[];launched=False
        try:
            created.append('a7c2-preview-staged.so');session.temp(created[-1],blob)
            if session.read('/tmp/jiritsu/'+created[-1])!=blob:raise RuntimeError('임시 모듈 리드백 불일치')
            if request:
                contents=struct.pack('<8sII',request[1],mode,0)
                created.append(request[0]);session.temp(request[0],contents)
                if session.read('/tmp/jiritsu/'+request[0])!=contents:raise RuntimeError('실행 모드 리드백 불일치')
            self.audit('native_about_to_run',module=name,mode=mode)
            created.extend(['libJit.so',result])
            if name=='runtime':created.append('a7c2.probe.result')
            launched=True
            session.temp('libJit.so',blob)
            raw=session.read('/tmp/jiritsu/'+result)
            dest=self.root/'evidence'/f'{datetime.now():%Y%m%d-%H%M%S-%f}-{name}.bin'
            dest.parent.mkdir(exist_ok=True);dest.write_bytes(raw)
            if dynamic:
                if byte_build is not None:
                    from looktool.checkpointworker import program_report
                    parsed=program_report(raw,mode,byte_build)
                else:
                    from compat import modules
                    parsed=modules()[3].validate_worker_report(raw,mode,self.state['compatibility']['build']['contract'])
            else:parsed=core.parse_native(name,raw,mode)
            self.audit('native_verified',module=name,mode=mode,result=parsed)
            return parsed
        except BaseException:
            if launched:session.healthy=False
            raise
        finally:
            for item in reversed(list(dict.fromkeys(created))):
                if not session.healthy:
                    self.audit('temporary_cleanup_pending',name=item);continue
                if session.read('/tmp/jiritsu/'+item,True) is not None:session.temp(item,remove=True)
                if session.read('/tmp/jiritsu/'+item,True) is not None:raise RuntimeError('임시 파일 정리가 완료되지 않았습니다')
    def prepare(self,second,firmware_version=core.VERSION):
        from transport import Session
        core.assets()
        if firmware_version!=core.VERSION:raise ValueError('FL23은 2.01 전용입니다. 카메라 버전 화면을 보고 입력하세요')
        if self.root.exists() and any(self.root.iterdir()):raise ValueError('기존 기록은 보존하고 새 빈 작업 폴더를 선택하세요')
        second=Path(second).resolve()
        if second==self.root or self.root in second.parents or second in self.root.parents:raise ValueError('두 번째 백업은 서로 포함되지 않는 별도 폴더를 사용하세요')
        if second.exists() and any(second.iterdir()):raise ValueError('두 번째 백업 폴더는 비어 있어야 합니다')
        self.root.mkdir(parents=True,exist_ok=True);second.mkdir(parents=True,exist_ok=True)
        (self.root/'original').mkdir();(self.root/'candidate').mkdir()
        self.state={'format':1,'model':core.MODEL,'version':firmware_version,'version_source':'user-camera-version-screen','phase':'preparing',
                    'backup_copy':str(second),'backup_files':{},'journal':[]}
        self.save('preparing')
        with Session() as session:
            self.state['identity']=session.identity
            event('A7C II를 인식했습니다. 원본 프로그램과 이 카메라의 설정을 백업하는 중입니다.')
            backup=session.call(session.camera.getBackupPresetData,True);records=core.backup_records(backup)
            originals={}
            for chip,name,path in [(1,core.PROGRAMS[1],'/cp/cpapp-b.bin'),(0,core.PROGRAMS[0],'/usr/lib/appFw.so')]:
                event('전체 읽기 '+name)
                data=session.read('/tmp/jiritsu/../..'+path)
                originals[name+'.bin']=data
            originals['backup.private.bin']=backup
            for key in core.CP_KEYS:
                data=session.cp_read(key)
                if data!=records[1][key].data:raise ValueError('네이티브 색상 리드백이 카메라 백업과 일치하지 않습니다')
                originals[f'unit-{key:08x}.bin']=data
            # Capture the initial rollback point before building candidates.
            # A later LUT job must not invalidate this independent archive.
            import compat
            compat.modules()
            from looktool import checkpoint
            saved=session.call(session.camera.getBackupPresetData,False)
            values=checkpoint.capture(session,{name:originals[name+'.bin'] for name in core.PROGRAMS},backup,saved)
            archive=self.root/'original/checkpoint.private.zip'
            checkpoint.write(archive,values,session.identity,provenance={'type':'fl23-prepare-before-changes'})
            checkpoint.copy(archive,second/'checkpoint.private.zip')
            self.state['checkpoint']=str(second/'checkpoint.private.zip')
            for name,data in originals.items():
                for parent in [self.root/'original',second]:
                    target=parent/name;target.write_bytes(data)
                    if target.read_bytes()!=data:raise ValueError('백업 재기록 검증 불일치')
                self.state['backup_files'][name]=len(data)
            self.save('backups_verified')
            self.state['compatibility']=compat.prepare(self.root,originals['appFw.so.bin'],originals['cpapp-b.bin.bin'],firmware_version)
            self.audit('version_and_patch_positions_checked',**self.state['compatibility']['identity'])
            enabled=self.state.get('compatibility',{}).get('already_enabled',False)
            for i,key in enumerate(core.AP_KEYS):
                expected=bytes([10+i]) if enabled else b'\0'
                if session.call(session.camera.readBackup,key)!=expected or records[0][key].data!=expected:
                    raise ValueError('예약 슬롯이 이미 사용 중입니다. 이 프리뷰 버전은 덮어쓰기를 거부합니다')
            units=(originals['unit-02a40001.bin'],originals['unit-02a4000d.bin'])
            if enabled:
                compat.validate_enabled_colors(*units);merged=units
            else:merged=core.merge_colors(*units)
            for key,data in zip(core.CP_KEYS,merged):(self.root/'candidate'/f'unit-{key:08x}.bin').write_bytes(data)
            self.save('backups_verified')
            self.module(session,'probe')
            if not enabled:
                self.module(session,'installer',0)
                self.module(session,'wbi',0)
        self.save('prepared')
        if enabled:event('FL2／FL3가 이미 활성화되어 있음을 확인했습니다. LUT／이름／커스텀 레시피를 유지하고 중복 쓰기는 하지 않았습니다. verify를 실행해 로드 상태를 확인하세요.')
        else:event('백업, 후보와 실기 검증만 완료했습니다. 아직 펌웨어를 수정하거나 촬영 설정을 저장하지 않았습니다.')
    def units(self):
        originals={k:(self.root/'original'/f'unit-{k:08x}.bin').read_bytes() for k in core.CP_KEYS}
        merged=core.merge_colors(originals[core.CP_KEYS[0]],originals[core.CP_KEYS[1]])
        return originals,dict(zip(core.CP_KEYS,merged))
    def files(self,session,after):
        for chip,name,path in [(1,core.PROGRAMS[1],'/cp/cpapp-b.bin'),(0,core.PROGRAMS[0],'/usr/lib/appFw.so')]:
            event('전체 리드백 '+name)
            data=session.read('/tmp/jiritsu/../..'+path)
            folder=self.root/'readback';folder.mkdir(exist_ok=True)
            stamp=f'{datetime.now():%Y%m%d-%H%M%S-%f}-{name}'
            (folder/stamp).write_bytes(data)
            original=(self.root/'original'/(name+'.bin')).read_bytes()
            if not after:expected=original
            elif 'compatibility' in self.state:expected=(self.root/'candidate'/name).read_bytes()
            else:expected=core.patch_program(original,chip)
            if data!=expected:raise ValueError('프로그램 전체 리드백 불일치. 이후 작업을 중지합니다')
        self.audit('host_full_file_readback_verified',after=after)
    def settings(self,session,restore=False):
        originals,merged=self.units();target=originals if restore else merged
        desired=dict(zip(core.AP_KEYS,[b'\0',b'\0'] if restore else [b'\x0a',b'\x0b']))
        before_raw=session.call(session.camera.getBackupPresetData,True);before=core.backup_records(before_raw)
        folder=self.root/'evidence';folder.mkdir(exist_ok=True)
        (folder/f'{datetime.now():%Y%m%d-%H%M%S-%f}-settings-before.private.bin').write_bytes(before_raw)
        current_cp={k:session.cp_read(k) for k in core.CP_KEYS}
        current_ap={k:session.call(session.camera.readBackup,k) for k in core.AP_KEYS}
        for key in core.CP_KEYS:
            accepted=[originals[key],merged[key]] if restore else [originals[key]]
            if current_cp[key] not in accepted or before[1][key].data!=current_cp[key]:raise ValueError('CP 설정 상태를 알 수 없어 쓰기를 거부합니다')
        for i,key in enumerate(core.AP_KEYS):
            accepted=[b'\0',bytes([10+i])] if restore else [b'\0']
            if current_ap[key] not in accepted or before[0][key].data!=current_ap[key]:raise ValueError('AP 설정 상태를 알 수 없어 쓰기를 거부합니다')
        for key in core.CP_KEYS:
            if current_cp[key]==target[key]:continue
            self.audit('about_to_write_cp',key=hex(key));session.cp_write(key,target[key])
            if session.cp_read(key)!=target[key]:raise ValueError('CP 리드백 실패')
        if any(current_cp[k]!=target[k] for k in core.CP_KEYS):
            self.audit('about_to_save_cp');session.call(session.camera.saveBackup,0x02a4)
        for key in core.AP_KEYS:
            if current_ap[key]==desired[key]:continue
            self.audit('about_to_write_ap',key=hex(key));session.ap_write(key,desired[key])
            if session.call(session.camera.readBackup,key)!=desired[key]:raise ValueError('AP 바인딩 리드백 실패')
        if any(current_ap[k]!=desired[k] for k in core.AP_KEYS):
            self.audit('about_to_save_ap');session.call(session.camera.saveBackup,0x02cf)
        after_raw=session.call(session.camera.getBackupPresetData,True)
        stored_raw=session.call(session.camera.getBackupPresetData,False)
        (folder/f'{datetime.now():%Y%m%d-%H%M%S-%f}-settings-after.private.bin').write_bytes(after_raw)
        (folder/f'{datetime.now():%Y%m%d-%H%M%S-%f}-settings-stored.private.bin').write_bytes(stored_raw)
        expected={(0,k):v for k,v in desired.items()};expected.update({(1,k):v for k,v in target.items()})
        preservation=core.verify_preservation(before,core.backup_records(after_raw),expected)
        stored=core.backup_records(stored_raw)
        for (chip,key),value in expected.items():
            if stored[chip][key].data!=value:raise ValueError('저장 후 리드백 불일치')
        self.audit('settings_storage_verified',restore=restore,**preservation)
    def install(self,battery=None,*,power_confirmed=False):
        from transport import Session
        self.load()
        if self.state['phase']!='prepared':raise ValueError('먼저 준비를 완료해야 합니다. 상태를 알 수 없을 때는 재실행할 수 없습니다')
        if self.state.get('compatibility',{}).get('already_enabled'):
            event('준비 단계에서 FL2／FL3가 이미 존재함을 확인했습니다. 설치는 실행하지 않았습니다. verify를 실행하세요.');return
        import compat
        event('알림: '+compat.WARNING)
        from looktool.power import require_power
        self.state['power_confirmation']=require_power(battery,confirmed=power_confirmed)
        with Session(self.state['identity']) as session:
            # Recheck every per-body target before any program-file write.
            originals,_=self.units();current=core.backup_records(session.call(session.camera.getBackupPresetData,True))
            if any(current[1][k].data!=v for k,v in originals.items()) or any(current[0][k].data!=b'\0' for k in core.AP_KEYS):
                raise ValueError('준비 후 카메라 대상 설정이 변경되었습니다. 덮어쓰지 마세요')
            self.module(session,'wbi',0)
            self.save('writing_programs');event('고정 영역에 쓰는 중입니다. 케이블을 뽑거나 전원을 끄지 마세요.')
            self.module(session,'installer',1)
            self.save('programs_written');self.files(session,True)
            self.save('writing_settings');self.settings(session)
            self.save('settings_verified');event('프로그램과 저장된 설정을 검증했습니다. 기존 부팅 스냅샷을 처리하는 중입니다.')
            self.module(session,'wbi',1)
        self.save('installed_pending_cold_start')
        event('설치 및 리드백 완료. 케이블을 뽑고 정상적으로 전원을 끈 뒤, 액세스 램프가 꺼지면 배터리를 10초간 분리했다가 다시 넣고 전원을 켜세요.')
        event('FL2/FL3를 각각 다시 설정하고, 새 사진을 찍어 재생해 보세요. 현재는 실험적 프리뷰 버전입니다.')
    def verify(self):
        from transport import Session
        self.load()
        with Session(self.state['identity']) as session:
            # Later LUT installs legitimately change these program files.
            # Verify FL23's current patches/parameters and loaded spans.
            programs={name:session.read('/tmp/jiritsu/../..'+path) for name,path in
                [(core.PROGRAMS[0],'/usr/lib/appFw.so'),(core.PROGRAMS[1],'/cp/cpapp-b.bin')]}
            import compat
            _,enabled,_,_,_=compat.plan(programs[core.PROGRAMS[0]],programs[core.PROGRAMS[1]],
                self.state.get('version',core.VERSION),transitions=False)
            if not enabled:raise ValueError('현재 프로그램에서 FL2／FL3가 완전히 활성화되지 않았습니다')
            current=core.backup_records(session.call(session.camera.getBackupPresetData,True))
            if self.state.get('compatibility',{}).get('already_enabled'):
                new={k:(self.root/'original'/f'unit-{k:08x}.bin').read_bytes() for k in core.CP_KEYS}
            else:_,new=self.units()
            if any(current[1][k].data!=v for k,v in new.items()):raise ValueError('재시작 후 CP 파라미터 불일치')
            if any(current[0][k].data!=bytes([10+i]) for i,k in enumerate(core.AP_KEYS)):raise ValueError('재시작 후 AP 바인딩 불일치')
            runtime=self.module(session,'runtime')
            candidate=programs[core.PROGRAMS[0]]
            for span in runtime['spans']:
                expected=candidate[span['offset']:span['offset']+span['length']].hex()
                if span['memory']!=expected or span['disk']!=expected:
                    raise ValueError('런타임에 이전 데이터가 남아 있습니다. 전원을 끄고 배터리를 분리한 뒤 다시 켰는지 확인하세요')
        self.audit('post_restart_files_and_targets_verified')
        event('디스크의 프로그램과 저장된 파라미터가 예상과 일치합니다. 메뉴, 사진 및 재생은 직접 확인해야 합니다.')

def main():
    p=argparse.ArgumentParser(description='A7C II 2.01 FL2/FL3 실험적 프리뷰')
    p.add_argument('command',choices=['prepare','install','verify','check-package'])
    p.add_argument('--workspace',type=Path);p.add_argument('--backup-copy',type=Path)
    p.add_argument('--firmware-version',help='prepare: firmware version from the camera version screen, e.g. 2.01')
    p.add_argument('--battery-percent',type=int,default=-1)
    args=p.parse_args()
    if args.command=='check-package':core.assets();event('패키지 에셋이 온전합니다. 이 검사는 카메라에 연결하지 않습니다.');return
    if not args.workspace:p.error('카메라 작업 폴더를 지정해야 합니다')
    job=Job(args.workspace)
    try:
        if args.command=='prepare':
            if not args.backup_copy:p.error('준비 단계에서는 두 번째 백업 폴더를 지정해야 합니다')
            if not args.firmware_version:p.error('--firmware-version 2.01 로 카메라 버전 화면의 버전을 입력하세요')
            job.prepare(args.backup_copy,args.firmware_version)
        elif args.command=='install':job.install(args.battery_percent)
        elif args.command=='verify':job.verify()
    except Exception as exc:
        if hasattr(job,'state'):
            job.state['last_error']=str(exc);job.state['failed_during']=job.state['phase']
            job.save('needs_review')
        event('중지됨: '+str(exc));event('무작정 쓰기를 재실행하거나 재부팅하지 마세요. 확인을 위해 작업 폴더를 보존하세요.')
        import traceback
        diagnostic=job.root/'evidence';diagnostic.mkdir(parents=True,exist_ok=True)
        (diagnostic/f'{datetime.now():%Y%m%d-%H%M%S-%f}-error.txt').write_text(traceback.format_exc(),encoding='utf-8')
        raise SystemExit(1)
if __name__=='__main__':main()
