"""Experimental electronic-shutter trial: explicit preflight/apply/restore.

Importing this file and status/help are offline. No camera I/O is performed
unless a physical command is explicitly invoked. Keep every job private.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import struct
import time
import zipfile

import candidate


def save(root, state):
    path = root / 'job.private.json'
    temp = path.with_suffix('.pending')
    temp.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, path)


def backup(root, copy, values):
    path = root / 'before.private.zip'
    with zipfile.ZipFile(path, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name, raw in values.items():
            z.writestr(name, raw)
    copy = Path(copy).resolve()
    if copy == root or root in copy.parents:
        raise ValueError('Put the second backup outside the working job')
    copy.mkdir(parents=True, exist_ok=True)
    target = copy / 'shutter-before.private.zip'
    with path.open('rb') as src, target.open('xb') as dst:
        shutil.copyfileobj(src, dst)
        dst.flush()
        os.fsync(dst.fileno())
    with zipfile.ZipFile(path) as z, zipfile.ZipFile(target) as zz:
        if z.namelist() != zz.namelist() or any(z.read(n) != values[n] or zz.read(n) != values[n] for n in values):
            raise ValueError('Backup reconstruction differs')
    return str(target)


def factory(expected=None):
    from replacement import active,BorrowedCamera
    current=active()
    if current is not None and not current.opening:return BorrowedCamera(current,expected)
    from looktool import devicesession
    class SoundCamera(devicesession.Camera):
        def _connect(self):
            transport, self.core = devicesession.modules()
            import usb.core
            import usb.backend.libusb1
            expected=({key:self.expected[key] for key in ('model','serial_sha256')}
                      if self.expected else None)
            # Sound jobs have always used Sony's service body identity. Do not
            # pass them through LUT USB-descriptor binding: that is a different
            # namespace and legacy sound jobs never stored that optional field.
            backend=usb.backend.libusb1.get_backend(find_library=transport.libusb_package.find_library)
            deadline=time.monotonic()+15
            while True:
                normal=usb.core.find(idVendor=0x054c,idProduct=0x0e8a,backend=backend)
                if normal is not None:
                    transport.usb.util.dispose_resources(normal)
                    self.link=transport.Session(expected)
                    break
                service=(usb.core.find(idVendor=0x054c,idProduct=0x0336,backend=backend)
                         if expected else None)
                if service is not None:
                    transport.usb.util.dispose_resources(service)
                    self.link=transport.RecoverySession(expected)
                    break
                if time.monotonic()>=deadline:
                    raise ValueError('카메라 USB 연결을 찾지 못했습니다. USB를 뽑았다 꽂고 대용량 저장장치를 선택한 뒤 다시 시도하세요. 아직 서비스 인증이나 사운드 쓰기를 하지 않았습니다.')
                time.sleep(.5)
            self.link.__enter__()
            self.identity = dict(self.link.identity)
            self.service_only = False
            return self
        def temp_read(self, name, missing=False):
            if name in ('shutter.staged.so', 'shutter.probe.result'):
                return self.link.read('/tmp/jiritsu/' + name, missing)
            return super().temp_read(name, missing)
        def temp_write(self, name, data):
            if name == 'shutter.staged.so':
                if not self.programs_pinned or not data or len(data) > 65536:
                    raise ValueError('Invalid sound preflight staging')
                self.link.select('/tmp/jiritsu/' + name)
                return self.link.jiritsu(16, b'\0\xff\0' + data)
            return super().temp_write(name, data)
        def temp_remove(self, name):
            from looktool.checkpointjob import TEMPS
            if name in TEMPS or name in ('shutter.staged.so', 'shutter.probe.result', 'shutter.custom.lease'):
                self.link.select('/tmp/jiritsu/' + name)
                return self.link.jiritsu(18, b'\0\xff')
            return super().temp_remove(name)
    return SoundCamera(expected, allow_service_resume=True)


def completed_readonly_failure(state):
    """Prove a failed native-enable attempt never reached a mutating worker.

    Mode 0 is read-only only for these three workers, not for audio registration.
    An interrupted staging/invocation/cleanup remains unresolved.
    """
    if (state.get('schema') != 'a7c2-shutter-job/1' or
            state.get('phase') != 'needs_review' or
            state.get('camera_firmware_written') is not False or
            not isinstance(state.get('journal'), list)):
        return False
    allowed = {'shutter.staged.so', 'lut-runtime-staged.so', 'lut-program-staged.so'}
    active = None
    step = None
    for event in state.get('journal', []):
        kind = event.get('event')
        stage = event.get('stage')
        if kind == 'native_stage_intent':
            if active is not None or stage not in allowed or event.get('mode') != 0:
                return False
            active, step = stage, 'staged'
        elif kind == 'native_intent':
            if stage != active or step != 'staged' or event.get('mode') != 0:
                return False
            step = 'invoked'
        elif kind == 'native_extra_result':
            if active is None or step != 'invoked':
                return False
        elif kind == 'native_result_verified':
            if stage != active or step != 'invoked' or event.get('mode') != 0:
                return False
            step = 'verified'
        elif kind == 'native_cleanup_verified':
            if stage != active or step != 'verified' or event.get('mode') != 0:
                return False
            active, step = None, None
        else:
            return False
    return active is None


def run_native(camera, root, state, blob, kind, mode=0, plan=None):
    from looktool import devicesession, checkpointworker
    def record(event, **fields):
        state.setdefault('journal', []).append({'event': event, **fields})
        save(root, state)
    if kind == 'probe':
        stage, request, result, contents = 'shutter.staged.so', None, 'shutter.probe.result', None
        decode = candidate.parse_probe
        extras = ('a7c2.probe.result',)
    elif kind == 'program':
        stage, request, result = 'lut-program-staged.so', 'a7c2.install.request', 'a7c2.install.result'
        contents = struct.pack('<8sII', b'A7INS001', mode, 0)
        decode = lambda raw: checkpointworker.program_report(raw, mode, plan)
        extras = ()
    else:
        stage, request, result = 'lut-runtime-staged.so', 'a7c2.wbi.request', 'a7c2.wbi.result'
        contents = struct.pack('<8sII', b'A7WBI001', mode, 0)
        decode = lambda raw: camera.core.parse_native('wbi', raw, mode)
        extras = ()  # The worker includes fresh drop-log evidence in its report.
    evidence = root / 'evidence'
    evidence.mkdir(exist_ok=True)
    return devicesession.invoke(camera, Path(blob).read_bytes(), stage=stage,
        request=request, result=result, contents=contents, mode=mode, decode=decode,
        record=record, save_raw=lambda raw: (evidence / (kind + '-' + str(len(state['journal'])) + '.bin')).write_bytes(raw), extras=extras)


def capture(camera):
    return {'appFw.so':camera.read('/usr/lib/appFw.so'),
            'cpapp-b.bin':camera.read('/cp/cpapp-b.bin'),
            'settings-ram.bin':camera.snapshot(True),
            'settings-saved.bin':camera.snapshot(False)}


def preflight(root, copy, zig=None, camera_factory=factory, *, expected=None, adopt_only=False):
    root.mkdir(parents=True, exist_ok=False)
    state = {'schema': 'a7c2-shutter-job/1', 'phase': 'preparing', 'firmware': '2.01',
             'camera_firmware_written': False, 'physical_sound_verified': False, 'journal': []}
    save(root, state)
    try:
        with (camera_factory(expected) if expected else camera_factory()) as camera:
            values = capture(camera)
            enabled=candidate.is_enabled(values['cpapp-b.bin'])
            current=candidate.is_current(values['cpapp-b.bin'])
            candidate.guard(values['appFw.so'], values['cpapp-b.bin'], installed=enabled)
            state['identity'] = camera.identity
            if adopt_only and not enabled:
                raise ValueError('전자 셔터음이 아직 켜져 있지 않습니다. 먼저 "전자 셔터음 켜기"를 누르고 전원을 완전히 끄고 다시 켠 뒤 미리 들어 보세요. 이번에는 펌웨어에 쓰거나 미리 듣기를 등록하지 않았습니다.')
            if adopt_only and not current:
                raise ValueError('이전 버전의 셔터음은 아직 기계식 셔터를 제한하지 않습니다. "전자 셔터음 켜기"를 다시 눌러 갱신한 뒤 미리 들어 보세요. 이번에는 사운드를 등록하지 않았습니다.')
            evidence=values
            if current:
                # Adoption performs no firmware write; keep small exact
                # branch/getter evidence rather than another complete image.
                evidence={'native-sound-branch.bin':b''.join(values['cpapp-b.bin'][at:at+len(old)]
                              for at,old,_ in candidate.WINDOWS),
                          'ap-sound-getters.bin':values['appFw.so'][0x286cde8:0x286cdfc]+values['appFw.so'][0x2885488:0x288549c]}
            state['backup_copy'] = backup(root, copy, evidence)
            plan, _ = candidate.build(values['appFw.so'], values['cpapp-b.bin'], root / 'build', zig)
            camera.programs_pinned = True
            probe = run_native(camera, root, state, root / 'build/probe.so', 'probe')
            state['probe'] = probe
            candidate.require_registry(probe)
            if current:
                for name,path in (('appFw.so','/usr/lib/appFw.so'),('cpapp-b.bin','/cp/cpapp-b.bin')):
                    if camera.read(path)!=values[name]:raise ValueError('Existing sound program changed during adoption')
                from looktool import devicesettings
                for key,memory in (('settings-ram.bin',True),('settings-saved.bin',False)):
                    devicesettings.compare(camera.records(values[key]),camera.records(camera.snapshot(memory)),{},camera.core)
                state.update(phase='written',adopted_existing_enabled=True,
                    complete_readback_and_settings_verified=True,camera_firmware_written=False,
                    electronic_only_gate=True,
                    backup_scope='existing native sound selector/getters; OFF uses a fresh inverse')
                save(root,state)
                return state
            if probe['image_mode'] == '2':
                run_native(camera, root, state, root / 'build/wbi.so', 'wbi', 0)
            native = plan['native_program']
            run_native(camera, root, state, native['path'], 'program', 0, native)
            state['phase'] = 'ready'
            state['camera_firmware_written'] = False
            save(root, state)
    except BaseException:
        state['phase'] = 'needs_review'
        save(root, state)
        raise
    return state


def change(root, *, restore=False, zig=None, camera_factory=factory):
    state = json.loads((root / 'job.private.json').read_text(encoding='utf-8'))
    allowed = ('written', 'reload_pending', 'restored') if restore else ('ready',)
    if state['phase'] not in allowed or (restore and state['phase'] == 'restored'):
        raise ValueError('Job is not at a known writable stage; inspect its evidence first')
    copy = Path(state['backup_copy'])
    with zipfile.ZipFile(root / 'before.private.zip') as a, zipfile.ZipFile(copy) as b:
        for name in a.namelist():
            if a.read(name) != b.read(name):
                raise ValueError('The two original backups differ')
    state['phase'] = 'restoring' if restore else 'applying'
    save(root, state)
    try:
        with camera_factory(state['identity']) as camera:
            fresh = capture(camera)
            plan, target = candidate.build(fresh['appFw.so'], fresh['cpapp-b.bin'],
                root / ('restore-build' if restore else 'apply-build'), zig, restore=restore)
            camera.programs_pinned = True
            probe = run_native(camera, root, state, root / 'build/probe.so', 'probe')
            candidate.require_registry(probe)
            from replacement import active
            if not restore and probe['audio_signals'] != 0 and active() is None:
                raise ValueError('For the first sound trial select Audio Signals On, then make a new preflight job')
            if probe['image_mode'] == '2':
                run_native(camera, root, state, root / 'build/wbi.so', 'wbi', 0)
            native = plan['native_program']
            run_native(camera, root, state, native['path'], 'program', 0, native)
            run_native(camera, root, state, native['path'], 'program', 1, native)
            state['program_transaction_verified'] = True
            state['camera_firmware_written'] = True
            save(root, state)
            for name, path in (('appFw.so', '/usr/lib/appFw.so'), ('cpapp-b.bin', '/cp/cpapp-b.bin')):
                if camera.read(path) != target[name]:
                    raise ValueError('Complete program readback differs: ' + name)
            from looktool import devicesettings
            for key, memory in (('settings-ram.bin', True), ('settings-saved.bin', False)):
                devicesettings.compare(camera.records(fresh[key]), camera.records(camera.snapshot(memory)), {}, camera.core)
            state['complete_readback_and_settings_verified'] = True
            state['phase'] = 'reload_pending'
            save(root, state)
            if probe['image_mode'] == '2':
                run_native(camera, root, state, root / 'build/wbi.so', 'wbi', 1)
            state['phase'] = 'restored' if restore else 'written'
            state['electronic_only_gate'] = not restore
            state['physical_sound_verified'] = False
            save(root, state)
    except BaseException:
        # Retain evidence; never automatically replay, roll back or power-cycle.
        state['phase'] = 'needs_review'
        save(root, state)
        raise
    return state


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('preflight', 'apply', 'restore', 'status'):
        s = sub.add_parser(name)
        s.add_argument('--job', required=True, type=Path)
        if name != 'status':
            s.add_argument('--zig')
        if name == 'preflight':
            s.add_argument('--firmware', required=True, choices=['2.01'])
            s.add_argument('--backup-copy', required=True, type=Path)
    a = p.parse_args()
    root = a.job.resolve()
    if a.command == 'preflight':
        state = preflight(root, a.backup_copy, a.zig)
    elif a.command == 'status':
        state = json.loads((root / 'job.private.json').read_text(encoding='utf-8'))
    else:
        state = change(root, restore=a.command == 'restore', zig=a.zig)
    print(json.dumps({k: state.get(k) for k in ('phase', 'camera_firmware_written', 'physical_sound_verified')}, indent=2))
    if a.command in ('apply', 'restore'):
        print('USB I/O ended. Disconnect all external power, shut down normally, wait for the access light, then remove the battery for at least one minute. Physical validation is still required.')


if __name__ == '__main__':
    main()
