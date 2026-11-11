"""Explicit sound-buffer and temporary PCM-registration research commands.

build is offline. allocate-release normally returns both empty buffers. --wav
adds an AP-to-CP-to-AP byte comparison. --temporary-register additionally replaces
kind3's current registration and retains its owned CP buffer until cold power-off;
AP's original descriptors, firmware files and settings remain untouched.
No command requests playback itself. Native functions have unbounded waits.
A missing/incomplete report is an unknown outcome, never permission to repeat.
"""
import argparse
import json
from pathlib import Path
import struct
import uuid

import candidate
import trial


def build(ap, output, zig=None, *, payload=None, register=False, wire_format=1, lease_token=None):
    from looktool import nativebuild
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    header = output / 'buffer.private.h'
    lines = []
    if register:
        if payload is None:
            raise ValueError('Temporary registration requires a PCM payload')
        if wire_format not in (0, 1):
            raise ValueError('Unsupported native wire format')
        lines += ['#define BUFFER_REGISTER 1', '#define REGISTER_WIRE_FORMAT ' + str(wire_format)]
        lease_token = uuid.uuid4().bytes if lease_token is None else lease_token
        if len(lease_token) != 16:
            raise ValueError('Temporary registration needs a 16-byte attempt token')
        lines.append('static const u8 lease_token[]={' + ','.join(map(str, lease_token)) + '};')
        (output / 'lease-token.private.bin').write_bytes(lease_token)
        for name, at, size in (('register_code', 0x34ec05c, 56), ('table_code', 0x2885488, 20)):
            lines.append('static const u8 ' + name + '[]={' + ','.join(map(str, ap[at:at + size])) + '};')
    if payload is not None:
        if not 0 < len(payload) <= 51200 or len(payload) % 64:
            raise ValueError('Round-trip payload needs 64-byte alignment within the native buffer capacity')
        lines += ['#define BUFFER_TRANSFER 1',
                  'static const u8 payload[]={' + ','.join(map(str, payload)) + '};']
    for name, at, size in (('alloc_code', 0x3552790, 56),
                           ('release_code', 0x3552978, 48)):
        code = ap[at:at + size]
        if len(code) != size:
            raise ValueError('Truncated original AP sound-buffer function')
        lines.append('static const u8 ' + name + '[]={' + ','.join(map(str, code)) + '};')
    if payload is not None:
        lines.append('static const u8 transfer_code[]={' +
                     ','.join(map(str, ap[0x3514f54:0x3514f54 + 56])) + '};')
    header.write_text('\n'.join(lines) + '\n', encoding='ascii')
    worker = output / 'buffer.so'
    nativebuild.compile_worker((Path(__file__).parent / 'native/buffer_probe.c'),
                               header, worker, zig, fingerprints=False)
    return worker


def parse(raw):
    if len(raw) not in (112, 144, 208):
        raise ValueError('Incomplete buffer trial report; do not repeat the trial')
    magic, version, phase, status = struct.unpack_from('<8sIIq', raw)
    register = magic == b'A7PCM001'
    dma = magic == b'A7DMA001' or register
    if version != 1 or (magic != b'A7BUF001' and not dma) or len(raw) != (208 if register else 144 if dma else 112):
        raise ValueError('Unexpected buffer trial report')
    result = {'phase': phase, 'status': status,
              'audio_registration_trial': register,
              'playback_requested_or_firmware_written': False, 'dma_trial': dma}
    for name, offset in (('ap', 24), ('cp', 56)):
        values = struct.unpack_from('<IIQIIII', raw, offset)
        result[name] = dict(zip(('type', 'handle', 'address', 'bytes', 'offset', 'status', 'reserved'), values))
    fields = struct.unpack_from('<6I', raw, 88)
    result.update(zip(('ap_release', 'cp_release', 'ap_called', 'cp_called',
                       'ap_release_called', 'cp_release_called'), fields))
    result['all_empty_buffers_released'] = (phase == 10 and status == 0 and
        all(result[name + '_called'] == result[name + '_release_called'] == 1
            and result[name]['status'] == result[name + '_release'] == 0
            for name in ('ap', 'cp')))
    if dma:
        names = ('forward_status', 'reverse_status', 'mapping_status', 'unmap_status',
                 'compared', 'payload_length', 'transfer_called', 'retain_leases')
        result.update(zip(names, struct.unpack_from('<8I', raw, 112)))
        result['roundtrip_byte_equal'] = (
            result['compared'] == 1 and not any(result[n] for n in
                ('forward_status', 'reverse_status', 'mapping_status', 'unmap_status', 'retain_leases')))
    if register:
        result['original_entry_hex'] = raw[144:192].hex()
        result.update(zip(('unregister_status', 'register_status', 'restore_status', 'cp_retained'),
                          struct.unpack_from('<4I', raw, 192)))
        result['temporary_pcm_registered'] = (phase == 10 and not status and
            result['compared'] == result['cp_retained'] == 1 and
            result['ap_release_called'] == 1 and result['ap_release'] == 0 and
            result['cp_release_called'] == 0 and not any(result[n] for n in
                ('unregister_status', 'register_status', 'forward_status', 'reverse_status',
                 'mapping_status', 'unmap_status', 'retain_leases')))
        result['audible_custom_sound_verified'] = False
    return result


def initial_marker(state):
    result = state['result']
    if not result.get('temporary_pcm_registered'):
        raise ValueError('Prior registration did not complete; inspect its native outcome')
    return marker_bytes(result,state.get('lease_token_hex',''))


def marker_bytes(result,token=''):
    """The ownership marker is fixed before allocation, including failed runs."""
    entry=bytes.fromhex(result['original_entry_hex'])
    if len(entry)!=48 or not 0<result['payload_length']<=51200 or result['payload_length']%64:
        raise ValueError('Invalid sound marker description')
    token=bytes.fromhex(token)
    if len(token) not in (0,16):raise ValueError('Invalid sound lease token')
    raw = bytearray(208)
    struct.pack_into('<8sIIq', raw, 0, b'A7PCM001', 1, 0, 0)
    for at in (48, 80, 88, 92, 112, 116, 120, 124, 192, 196, 200):
        struct.pack_into('<I', raw, at, 0xffffffff)
    struct.pack_into('<I', raw, 132, result['payload_length'])
    raw[144:192] = entry
    return bytes(raw)+token


def preallocation_marker_rejection(state, raw):
    """The worker returned EEXIST before allocation, DMA or registration."""
    if (state.get('schema') != 'a7c2-empty-sound-buffer-trial/1' or
            state.get('camera_firmware_written') is not False):
        return False
    result=parse(raw)
    if not (result.get('audio_registration_trial') and result['phase']==1 and result['status']==-17
            and not any(result[n] for n in ('ap_called','cp_called','transfer_called',
                                           'ap_release_called','cp_release_called','cp_retained'))
            and result['unregister_status']==result['register_status']==0xffffffff):
        return False
    events=[event for event in state.get('journal',[]) if event.get('event')!='native_extra_result']
    if [event.get('event') for event in events]!=[
            'native_stage_intent','native_intent','native_result_verified','native_cleanup_verified']:
        return False
    if any(e.get('stage')!='shutter.staged.so' or e.get('mode')!=0 for e in events):
        return False
    return all(events[2].get('result',{}).get(k)==v for k,v in result.items())


def stopped_before_native(state, root):
    """The audition stopped before its native worker was staged or invoked.

    No audio registration, allocation or transfer can have happened. Steps
    before the worker only read or replace /tmp markers, which a cold
    power-off clears and the next attempt re-reads from the camera.
    """
    if (state.get('schema') != 'a7c2-empty-sound-buffer-trial/1' or state.get('phase') != 'needs_review'
            or state.get('camera_firmware_written') is not False or 'result' in state):
        return False
    if (Path(root) / 'report.private.bin').exists():
        return False
    return not any(event.get('event') in ('native_stage_intent', 'native_intent')
                   for event in state.get('journal', []))


def replace_completed_marker(camera, root, previous_jobs):
    """Replace an exact owned marker from a completed prior registration.

    Do not release its old CP handle: it may be stale after a power cycle.
    The new registration replaces kind3 normally; leased CP storage is reclaimed
    by cold power-off. Unknown/in-progress registrations are never replayed.
    """
    actual=camera.read('/tmp/jiritsu/shutter.custom.lease',True)
    if actual is None:
        return dict(previous_marker_absent=True)
    root=Path(root)
    (root/'replaced-marker.before.private.bin').write_bytes(actual)
    for path in previous_jobs:
        path=Path(path)
        try:
            previous=json.loads((path/'job.private.json').read_text(encoding='utf-8'))
            if (not previous.get('identity',{}).get('serial_sha256') or
                    any(previous['identity'].get(k)!=camera.identity.get(k) for k in ('model','serial_sha256'))
                    or initial_marker(previous)!=actual):
                continue
            # Decode the completed native report rather than relying on a
            # title, marker signature or a local phase label alone.
            report=parse((path/'report.private.bin').read_bytes())
            if not report.get('temporary_pcm_registered') or initial_marker(dict(previous,result=report))!=actual:
                continue
        except (OSError,ValueError,KeyError,struct.error):
            continue
        for name in ('shutter.staged.so','shutter.probe.result','libJit.so','a7c2.probe.result'):
            if camera.temp_read(name,True) is not None:
                raise ValueError('Existing native execution needs review before replacing sound: '+name)
        camera.temp_remove('shutter.custom.lease')
        if camera.read('/tmp/jiritsu/shutter.custom.lease',True) is not None:
            raise ValueError('Known prior sound marker replacement did not complete')
        return dict(completed_prior_marker_replaced=True,previous_job=str(path),
                    previous_cp_handle_released=False,camera_firmware_written=False)
    raise ValueError('기존 사운드 표식이 완료된 미리 듣기 보고서와 대응되지 않아 원래 표식을 저장했습니다. 알 수 없는 실행 작업은 덮어쓰지 않았습니다.')


def retire_recovered_marker(camera, root, previous_job):
    previous = json.loads((Path(previous_job) / 'job.private.json').read_text(encoding='utf-8'))
    observation = previous.get('owner_observation', {})
    recovered = previous.get('owner_cold_recovery_confirmed') or (
        previous.get('temporary_CP_lease_ended_by_owner_cold_power_off') and
        observation.get('original_sound_after_cold_power_off') == 'restored' and
        observation.get('shooting_and_playback_after_cold_power_off') == 'normal')
    if not recovered or previous['identity'] != camera.identity:
        raise ValueError('Retire only a same-body marker after owner-confirmed cold recovery')
    actual = camera.read('/tmp/jiritsu/shutter.custom.lease', True)
    if actual is None:
        return {'previous_marker_absent': True, 'camera_memory_or_firmware_changed': False}
    if actual != initial_marker(previous):
        raise ValueError('Marker belongs to another or unknown attempt; preserve it')
    (Path(root) / 'retired-marker.before.private.bin').write_bytes(actual)
    camera.temp_remove('shutter.custom.lease')
    if camera.read('/tmp/jiritsu/shutter.custom.lease', True) is not None:
        raise ValueError('Known recovered marker removal not confirmed')
    return {'same_attempt_marker_removed_after_owner_cold_recovery': True,
            'previous_job': str(Path(previous_job).resolve()),
            'camera_memory_or_firmware_changed': False}


def run(root, original_job, zig=None, *, payload=None, register=False, wire_format=1, recovered_job=None,
        replace_jobs=()):
    if register and wire_format != 0:
        raise ValueError('The first PCM16/format-1 trial produced noise or wrong sound. '
                         'Temporary audio registration is disabled pending decoder/layout proof; '
                         'offline build and non-playing buffer round trips remain available.')
    if register and (not payload or len(payload) % 64 or any(
            payload[n] > 8 for n in range(0, len(payload), 32))):
        raise ValueError('The next native-codec trial only accepts explicit filter0 blocks')
    from looktool import devicesession
    # A new directory and a durable pre-invocation state prevent an automatic
    # retry after USB timeout or missing native reply.
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    original = json.loads((Path(original_job) / 'job.private.json').read_text(encoding='utf-8'))
    if original['phase'] != 'written' or not original.get('complete_readback_and_settings_verified'):
        raise ValueError('Use the completed native-sound job for body binding')
    state = {'schema': 'a7c2-empty-sound-buffer-trial/1', 'phase': 'preparing',
             'identity': original['identity'], 'camera_firmware_written': False,
             'wire_format': wire_format, 'journal': []}
    token = uuid.uuid4().bytes if register else None
    if token is not None:
        state['lease_token_hex'] = token.hex()
    if payload is not None:
        (root / 'payload.private.bin').write_bytes(payload)
    trial.save(root, state)
    try:
        with trial.factory(original['identity']) as camera:
            if recovered_job is not None:
                state['prior_marker_retirement'] = retire_recovered_marker(camera, root, recovered_job)
                trial.save(root, state)
            ap = camera.read('/usr/lib/appFw.so')
            cp = camera.read('/cp/cpapp-b.bin')
            if cp[candidate.OFFSET:candidate.OFFSET+2]==candidate.BEFORE:
                raise ValueError('현재 카메라의 전자 셔터음이 아직 켜져 있지 않습니다. 먼저 켜세요. 이번에는 미리 듣기 사운드를 등록하지 않았습니다.')
            candidate.guard(ap, cp, installed=True)
            if register and not candidate.is_current(cp):
                raise ValueError('기계식 셔터 필터를 갱신하려면 전자 셔터음을 다시 켠 뒤 미리 들어 보세요. 이번에는 사운드를 등록하지 않았습니다.')
            worker = build(ap, root / 'build', zig, payload=payload, register=register, wire_format=wire_format, lease_token=token)
            camera.programs_pinned = True
            if register and replace_jobs and recovered_job is None:
                state['prior_marker_replacement']=replace_completed_marker(camera,root,replace_jobs)
                trial.save(root,state)

            def record(event, **fields):
                state['journal'].append({'event': event, **fields})
                trial.save(root, state)

            state['phase'] = 'invoking_empty_allocations'
            trial.save(root, state)
            result = devicesession.invoke(camera, worker.read_bytes(),
                stage='shutter.staged.so', request=None,
                result='shutter.probe.result', contents=None, mode=0, decode=parse,
                record=record, save_raw=lambda raw: (root / 'report.private.bin').write_bytes(raw),
                extras=('a7c2.probe.result',))
            state['result'] = result
            if register:
                if not result.get('temporary_pcm_registered'):
                    raise ValueError('Temporary registration incomplete: phase=%s status=%s, '
                        'AP_called=%s CP_called=%s transfer_called=%s, unregister=%s register=%s; '
                        'inspect the completed native report before another attempt' % (
                        result['phase'],result['status'],result['ap_called'],result['cp_called'],
                        result['transfer_called'],result['unregister_status'],result['register_status']))
                state['phase'] = 'temporary_pcm_registered_cold_reload_required_after_test'
                trial.save(root, state)
                return state
            if not result['all_empty_buffers_released']:
                raise ValueError('Empty buffer trial incomplete; inspect its report before any further operation')
            if payload is not None and not result.get('roundtrip_byte_equal'):
                raise ValueError('Audio buffer round trip did not compare equal')
            state['phase'] = 'roundtrip_buffers_released' if payload is not None else 'empty_buffers_released'
            trial.save(root, state)
    except BaseException:
        state['phase'] = 'needs_review'
        trial.save(root, state)
        raise
    return state


def confirm(root, result):
    """Record the owner's listening result offline; never infer it from an ACK."""
    root = Path(root)
    state = json.loads((root / 'job.private.json').read_text(encoding='utf-8'))
    if state['phase'] != 'temporary_pcm_registered_cold_reload_required_after_test':
        raise ValueError('Use the completed current temporary-registration job')
    if result == 'correct' and state.get('wire_format') != 0:
        raise ValueError('The PCM16/format1 route has no validated playback implementation')
    state['owner_listening_result'] = result
    state['owner_audio_correct'] = result == 'correct'
    state['owner_source_recognized'] = result in ('correct', 'quiet')
    state['phase'] = ('owner_requested_gain_adjustment_cold_reload_required' if result == 'quiet' else
                      'owner_confirmed_audio_cold_reload_required' if result == 'correct' else 'owner_audio_failed_cold_reload_required')
    state['next_physical_action'] = 'Cold power-off clears the temporary registration and CP lease; confirm native sound/shooting, then reconnect only for the next explicit operation.'
    trial.save(root, state)
    return state


def confirm_recovery(root):
    root = Path(root)
    state = json.loads((root / 'job.private.json').read_text(encoding='utf-8'))
    if state['phase'] not in ('owner_confirmed_audio_cold_reload_required', 'owner_audio_failed_cold_reload_required',
                              'owner_requested_gain_adjustment_cold_reload_required'):
        raise ValueError('First record the completed temporary trial listening result')
    state['owner_cold_recovery_confirmed'] = True
    state['temporary_CP_lease_ended_by_owner_cold_power_off'] = True
    state['phase'] = 'owner_confirmed_cold_recovered'
    trial.save(root, state)
    return state


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build')
    b.add_argument('--ap', type=Path, required=True)
    b.add_argument('--output', type=Path, required=True)
    b.add_argument('--zig')
    source_b = b.add_mutually_exclusive_group()
    source_b.add_argument('--wav', type=Path, help='PCM16 round-trip payload; format1 registration failed physically')
    source_b.add_argument('--native-bin', type=Path, help='Explicit format0 native-codec research payload')
    b.add_argument('--temporary-register', action='store_true')
    r = sub.add_parser('allocate-release')
    r.add_argument('--job', type=Path, required=True)
    r.add_argument('--native-sound-job', type=Path, required=True)
    r.add_argument('--zig')
    source_r = r.add_mutually_exclusive_group()
    source_r.add_argument('--wav', type=Path, help='Optional PCM16 round-trip payload; no playback')
    source_r.add_argument('--native-bin', type=Path, help='Explicit filter0 format0 research payload')
    r.add_argument('--temporary-register', action='store_true',
                   help='Only the filter0 format0 research route is enabled; PCM16/format1 remains disabled')
    r.add_argument('--after-recovered-job', type=Path,
                   help='Retire only that exact attempt marker after already recorded owner-confirmed cold recovery')
    c = sub.add_parser('confirm', help='Record the actual owner listening result; offline, no camera access')
    c.add_argument('--job', type=Path, required=True)
    c.add_argument('--result', required=True, choices=('correct', 'quiet', 'noise', 'wrong-speed', 'native', 'silent'),
                   help='correct means source content/pitch/speed and shooting/playback all verified by the owner')
    recovery = sub.add_parser('confirm-recovery', help='Offline owner statement: cold power-off completed and native sound/shooting/playback are normal')
    recovery.add_argument('--job', type=Path, required=True)
    a = p.parse_args(argv)
    if a.command == 'confirm-recovery':
        state = confirm_recovery(a.job)
        print(json.dumps({'phase': state['phase'], 'camera_io': False}, indent=2))
        return
    if a.command == 'confirm':
        state = confirm(a.job, a.result)
        print(json.dumps({k: state[k] for k in ('phase', 'owner_audio_correct', 'next_physical_action')}, indent=2))
        return
    payload = None
    if a.wav:
        from custom_sound import pcm_candidate
        payload = pcm_candidate(a.wav, 3)[0]
    elif a.native_bin:
        payload = a.native_bin.read_bytes()
    wire_format = 0 if a.native_bin else 1
    if a.command == 'build':
        print(build(a.ap.read_bytes(), a.output, a.zig, payload=payload, register=a.temporary_register, wire_format=wire_format))
    else:
        print(json.dumps(run(a.job, a.native_sound_job, a.zig, payload=payload, register=a.temporary_register, wire_format=wire_format, recovered_job=a.after_recovered_job), indent=2))


if __name__ == '__main__':
    main()
