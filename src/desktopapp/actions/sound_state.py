"""Inspect currently loaded sound code without an installation-job prerequisite."""
from pathlib import Path
import sys
import uuid
from ..paths import ROOT,write_json


def verify(args,job):
    sys.path.insert(0,str(ROOT/'src/shutter_sound'))
    import boot_sound,boot_trial,candidate,trial
    from replacement import CurrentRequest
    from looktool.devicesession import invoke
    root=Path(args['workspace'])/('inspect-'+uuid.uuid4().hex[:8]);root.mkdir(parents=True)
    state=dict(schema='a7c2-current-sound-check/1',history=[],pending=None,journal=[])
    def record(event,**fields):
        state['journal'].append(dict(event=event,**fields))
        write_json(root/'inspection.private.json',state)
    with CurrentRequest(root,state,'verify'):
        with trial.factory() as camera:
            ap=camera.read('/usr/lib/appFw.so');cp=camera.read('/cp/cpapp-b.bin')
            enabled=candidate.is_enabled(cp);candidate.guard(ap,cp,installed=enabled)
            custom=boot_sound.installed(ap)
            probe=boot_trial.current_probe(ap,root/'probe')
            camera.programs_pinned=True
            loaded=invoke(camera,Path(probe).read_bytes(),stage='lut-runtime-staged.so',request=None,
                result='shutter.probe.result',contents=None,mode=0,decode=candidate.parse_probe,
                record=record,save_raw=lambda raw:(root/'report.private.bin').write_bytes(raw),extras=('a7c2.probe.result',))
            candidate.require_registry(loaded)
            size=0
            if custom:
                current=boot_trial.current_sound(camera.read(boot_trial.PATHS[1],True))
                if current is None:raise ValueError('현재 커스텀 사운드 구성이 없습니다. 다시 저장하거나 순정 효과음으로 복원하세요.')
                path,size=current
                if len(camera.read(path))!=size:raise ValueError('현재 사운드 파일 길이가 구성과 다릅니다.')
                descriptor=loaded['entries'][2]
                if (descriptor['kind']!=3 or descriptor['length']!=size or
                        descriptor['filename_prefix']!='/usr' or not descriptor['native_handle']&0x8000):
                    raise ValueError('현재 사운드가 아직 디스크 구성대로 로드되지 않았습니다. 쓰기 후 안내에 따라 전원을 완전히 끄고 다시 켜세요.')
    return dict(phase='owner_confirmed_working' if args.get('accepted') else 'loaded_owner_confirmation_pending',
        workspace=str(root),camera_io=True,camera_firmware_written=False,camera_settings_written=False,
        sound_support_enabled_on_disk=enabled,custom_sound_on_disk=custom,audio_bytes=size,
        loaded_code_and_current_descriptor_verified=True)
