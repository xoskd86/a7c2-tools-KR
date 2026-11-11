"""Local factory transaction discovery and accounted temporary-state cleanup.

No device access at import/discovery. Recovery re-plans from current bytes;
it never replays a request with an unknown outcome.
"""
import json
import os
from pathlib import Path
import re
from . import checkpointworker, nameworker, nativebuild, worker

FINISHED = {'AWAITING_RELOAD', 'LOADED', 'COMPLETE'}


def supersede_temporary(camera, folder, record):
    """Archive inert project work files before planning a NEW factory target.

    This does not infer success of, or replay, the former operation. The caller
    captures current programs/settings/resources AFTER cleanup and builds new
    byte-checked writes from the built-in reference. Never replace libJit.so.
    """
    from .devicesession import TEMP_NAMES
    from .checkpointjob import TEMPS
    names=sorted((TEMP_NAMES | TEMPS)-{'libJit.so'})
    actual={name:camera.temp_read(name,True) for name in names}
    actual={name:raw for name,raw in actual.items() if raw is not None}
    if not actual:return 0
    if camera.temp_read('libJit.so',True) is not None:
        raise ValueError('카메라 실행 플러그인이 아직 남아 있어 실행 중에는 덮어쓸 수 없습니다. 이번에는 복원 쓰기를 하지 않았습니다. 연결을 끝낸 뒤 전원을 완전히 차단하고 다시 시도하세요')
    if camera.read('/etc/ld.so.preload',True):
        raise ValueError('카메라에 활성화된 preload가 있어 임시 작업 파일을 덮어쓰거나 복원 쓰기를 시작하지 않았습니다')
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    # Save every captured byte before removing anything on the camera. These
    # are tiny private worker/request/report files, not a body firmware backup.
    for name,raw in actual.items():
        path=folder/name
        with path.open('xb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        if path.read_bytes()!=raw:raise ValueError('Temporary archive readback differs: '+name)
    record('factory_temporary_archived',files={name:len(raw) for name,raw in actual.items()},
           old_operation_replayed=False,previous_outcome_inferred=False)
    # Check the whole namespace before deletion. A newly appearing report or
    # loader means it is changing, not merely leftover data.
    if camera.temp_read('libJit.so',True) is not None:
        raise ValueError('카메라 실행 플러그인이 나타나 진단을 저장했으며, 작업 파일은 덮어쓰지 않았습니다')
    for name in names:
        if camera.temp_read(name,True)!=actual.get(name):
            raise ValueError('카메라 임시 상태가 계속 바뀌고 있어 진단을 저장했으며, 작업 파일은 덮어쓰지 않았습니다: '+name)
    # Keep reports until last if the connection fails during cleanup.
    for name in sorted(actual,key=lambda value:value.endswith('.result')):
        if camera.temp_read(name,True)!=actual[name]:
            raise ValueError('Temporary state changed during factory cleanup: '+name)
        camera.temp_remove(name)
        if camera.temp_read(name,True) is not None:
            raise ValueError('Temporary cleanup incomplete: '+name)
    record('factory_temporary_superseded',files=list(actual),old_operation_replayed=False)
    print('이전 임시 작업 파일은 진단을 저장한 뒤 정리했습니다. 카메라의 현재 상태를 다시 읽고 내장 공장 기준으로 바로 복원 계획을 만들며, 이전 작업을 재실행하지 않습니다.',flush=True)
    return len(actual)


def prewrite_temporary_conflict(state):
    """A fresh restore stopped on shared names before any changing worker."""
    if (state.get('mode')!='factory_reset' or state.get('phase')!='NEEDS_REVIEW'
            or state.get('failed_during') not in ('READING','PLANNED','PREFLIGHT_VERIFIED')
            or state.get('resume_from')):
        return False
    if state.get('error') not in {
        'Existing temporary state needs recovery: '+name for name in
        ('lut-names-staged.so','a7c2.names.request','a7c2.names.result')
    } | {'카메라 실행 플러그인이 아직 남아 있어 실행 중에는 덮어쓸 수 없습니다. 이번에는 복원 쓰기를 하지 않았습니다. 연결을 끝낸 뒤 전원을 완전히 차단하고 다시 시도하세요'}:return False
    outstanding=set()
    for event in state.get('journal',[]):
        kind=event.get('event','')
        if kind.endswith('native_intent') and event.get('mode')!=0:return False
        if kind.endswith('native_stage_intent'):
            if event.get('mode')!=0:return False
            outstanding.add((kind[:-len('stage_intent')],event.get('stage')))
        elif kind.endswith('native_cleanup_verified'):
            outstanding.discard((kind[:-len('cleanup_verified')],event.get('stage')))
    return not outstanding


def connection_failed_before_restore(state):
    return (state.get('mode')=='factory_reset' and state.get('phase')=='NEEDS_REVIEW'
            and state.get('failed_during')=='READING' and state.get('journal')==[]
            and not state.get('identity') and not state.get('resume_from'))


def chain(folder):
    result, seen = [], set()
    while folder:
        root = Path(folder).resolve()
        if root in seen:
            raise ValueError('Recovery transaction ancestry contains a loop')
        seen.add(root)
        state = json.loads((root / 'direct.private.json').read_text(encoding='utf-8'))
        if state.get('mode') != 'factory_reset':
            raise ValueError('Not a factory recovery transaction')
        result.append((root, state))
        folder = state.get('resume_from')
    return list(reversed(result))


def pending(work_root, preferred=None):
    """Find the latest meaningful transaction per body, ignoring read-only failures.

    Old records are evidence for a restore, never a precondition: one that
    cannot be read or attributed is skipped (and kept), so the restore button
    re-plans from the connected camera instead of stopping on stale state.
    """
    paths = set(Path(work_root).glob('*/direct.private.json'))
    if preferred and Path(preferred, 'direct.private.json').is_file():
        paths.add(Path(preferred, 'direct.private.json'))
    rows = []
    for path in paths:
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            print('로컬 기록을 읽을 수 없어 자동 이어 가기에 사용하지 않았습니다(기록은 보존): ' + str(path), flush=True)
            continue
        if not isinstance(state, dict):
            continue
        if state.get('mode') != 'factory_reset' or not state.get('identity'):
            continue
        journal = state.get('journal', [])
        stages = [i for i, r in enumerate(journal) if r.get('event') == 'native_stage_intent']
        unfinished_native = bool(stages) and not any(r.get('event') == 'native_cleanup_verified' for r in journal[stages[-1] + 1:])
        meaningful = (state.get('phase') in FINISHED or bool(state.get('resume_from')) or
                      unfinished_native or any(r.get('event') == 'native_intent' and r.get('mode') in (1, 2) for r in journal))
        if not meaningful:
            continue
        identity = state['identity']
        key = (identity.get('model'), identity.get('serial_sha256') or identity.get('usb_serial_sha256'))
        if not all(key):
            print('복원 기록에 카메라 본체 식별 정보가 없어 자동 이어 가기에 사용하지 않았습니다(기록은 보존): ' + str(path), flush=True)
            continue
        rows.append((path.stat().st_mtime_ns, str(path), key, path.parent.resolve(), state))
    latest = {}
    for row in sorted(rows):
        latest[row[2]] = row
    # LOADED + verified RO is already a completed device recovery. Physical
    # photo acceptance remains separate; it must not keep hijacking future
    # Restore clicks after the owner installs other features again.
    active = [r for r in latest.values() if not (
        r[4].get('phase') == 'COMPLETE' or
        (r[4].get('phase') == 'LOADED' and r[4].get('readonly_after_reload_verified') is True))]
    if preferred:
        selected = Path(preferred).resolve()
        for row in active:
            try:
                ancestry = [root for root, _ in chain(row[3])]
            except (OSError, ValueError):
                ancestry = [row[3]]
            if selected in ancestry:
                return row[3], row[4]
    if len(active) > 1:
        # Which body is connected is unknown until USB opens; a fresh restore
        # binds to the connected body and re-plans from its current bytes.
        print('여러 카메라 본체의 미완료 복원 기록이 있습니다(모두 보존). 이번에는 현재 연결된 카메라를 기준으로 복원 계획을 새로 만듭니다: ' +
              '; '.join(str(r[3]) for r in active), flush=True)
        return None
    return (active[0][3], active[0][4]) if active else None


def known_programs(current, history):
    """Every previously attempted window must be exactly old or target bytes."""
    for root, state in history:
        for build in state.get('program_builds', []):
            path = Path(build['path'])
            if path.parent.resolve() != (root / 'worker').resolve():
                raise ValueError('Program evidence is outside its transaction')
            text = path.with_suffix('.private.h').read_text(encoding='ascii')
            arrays = {(kind, int(i)): bytes(map(int, values.split(','))) for kind, i, values in
                      re.findall(r'static const u8 (old|new)_(\d+)\[\]=\{([\d,]+)\};', text)}
            rows = re.findall(r'\{(\d+)UL,(\d+),([01]),old_(\d+),new_(\d+)\}', text)
            if len(rows) != build['windows']:
                raise ValueError('Incomplete recovery window evidence')
            for at, size, chip, old, new in rows:
                a, b = arrays['old', int(old)], arrays['new', int(new)]
                image = current[worker.NAMES[int(chip)]]
                if len(image) != build['sizes'][int(chip)] or len(a) != int(size) or len(b) != int(size):
                    raise ValueError('Recovery program layout differs')
                if image[int(at):int(at) + int(size)] not in (a, b):
                    raise ValueError('프로그램 영역에 확인되지 않은 바이트가 있습니다. 기록을 보존하며 쓰기를 자동으로 재실행할 수 없습니다')


def outcome(raw, result, mode, state, camera, *, allow_completed_name_mount_busy=False):
    """Require a complete native report with successful cleanup/remount.

    An operation may have failed; re-planning handles its actual current bytes.
    Cleanup/remount failures and incomplete reports require inspection.
    """
    if result == 'checkpoint.program.result':
        if len(raw) != 240:
            raise ValueError('Incomplete native program result')
        v = worker.REPORT.unpack_from(raw)
        sizes = [b['sizes'] for b in state.get('program_builds', [])]
        if (v[:3] != (b'A7IRP001', 1, mode) or not 1 <= v[3] <= 9
                or any(v[i] for i in (6, 7, 8, 9, 10)) or v[11] <= 0
                or list(v[14:16]) not in sizes):
            raise ValueError('Native program did not establish a clean completed outcome')
    elif result == 'checkpoint.names.result' and mode == 5:
        # Backup cleanup only deletes stale project backups; any complete
        # report that restored the read-only mount is accounted for.
        if len(raw) != nameworker.REPORT.size:
            raise ValueError('Incomplete backup cleanup result')
        v = nameworker.REPORT.unpack(raw)
        if v[:3] != (b'A7NMR001', 1, 5) or not 1 <= v[3] <= 9 or any(v[i] for i in (6, 7, 8, 9, 10)) or v[11] <= 0:
            raise ValueError('Backup cleanup remount is unconfirmed')
    elif result == 'checkpoint.names.result':
        if len(raw) != nameworker.REPORT.size:
            raise ValueError('Incomplete native name result')
        v = nameworker.REPORT.unpack(raw)
        build = state.get('inverse_name_build') or {}
        if allow_completed_name_mount_busy and checkpointworker.names_completed_mount_busy(raw, mode, build):
            return {'readonly_deferred_busy': True}
        if (v[:3] != (b'A7NMR001', 1, mode) or not 1 <= v[3] <= 9 or v[4] != build.get('count')
                or any(v[i] for i in (6, 7, 8, 9, 10, 13, 14, 17)) or v[11] <= 0
                or any(n > v[4] for n in v[12:17])):
            raise ValueError('Native name cleanup/remount is unconfirmed')
    elif result == 'a7c2.runtime.result':
        camera.core.parse_native('runtime', raw)
    elif result == 'a7c2.wbi.result':
        camera.core.parse_native('wbi', raw, mode)
    else:
        raise ValueError('Unsupported outstanding native operation: ' + str(result))
    return {}


def completion_evidence(camera, history, root, state, intent, later):
    """An empty /tmp alone does not establish that execution has finished."""
    if any(r.get('event') == 'native_result_verified' for r in later):
        return 'recorded-native-completion'
    for _, entry in history:
        for event in entry.get('journal', []):
            if (event.get('event') in ('pending_temporary_cleanup_verified', 'failed_name_temporary_cleanup_verified')
                    and event.get('previous_workspace', event.get('failed_workspace')) == str(root)):
                return 'recorded-temporary-cleanup'
    # Names/runtime/WBI modes run at most once per direct transaction.
    # Program batches share one result name, so an older batch report cannot
    # prove completion of the most recent program invocation.
    if intent['result'] != 'checkpoint.program.result':
        matching = set()
        for path in (root / 'evidence').glob('*.bin'):
            raw = path.read_bytes()
            try:
                outcome(raw, intent['result'], intent['mode'], state, camera)
            except (ValueError, KeyError, IndexError):
                continue
            matching.add(raw)
        if len(matching) == 1:
            return 'complete-saved-native-report'
    old_boot = state.get('source_boot_id', '').strip()
    if old_boot:
        raw = camera.read('/proc/sys/kernel/random/boot_id', True)
        boot = raw.decode('ascii', errors='replace').strip() if raw else ''
        if re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', boot) and boot != old_boot:
            return 'new-camera-boot'
    raise ValueError('임시 파일과 결과가 모두 없어 실행 완료나 새 부팅의 증거가 없습니다. 다시 쓰지 않으니 트랜잭션 기록을 보존하세요')


def cleanup(camera, history, record, save_raw, *, allow_completed_name_mount_busy=False):
    """Account for the most recent invocation before removing exact owned /tmp files."""
    outstanding = None
    for root, state in history:
        journal = state.get('journal', [])
        stages = [i for i, r in enumerate(journal) if r.get('event') == 'native_stage_intent']
        if stages:
            index = stages[-1]
            later = journal[index + 1:]
            outstanding = None if any(r.get('event') == 'native_cleanup_verified' for r in later) else (root, state, journal[index], later)
    if outstanding is None:
        return
    root, state, intent, later = outstanding
    stage, request, result = intent['stage'], intent.get('request'), intent['result']
    if stage != 'checkpoint.staged.so' or request not in (None, 'checkpoint.program.request', 'checkpoint.names.request', 'a7c2.wbi.request'):
        raise ValueError('Unsupported outstanding recovery namespace')
    if result == 'checkpoint.program.result':
        blobs = [Path(b['path']).read_bytes() for b in state.get('program_builds', [])]
    elif result == 'checkpoint.names.result' and intent.get('mode') == 5:
        blobs = [Path(state['purge_build']['path']).read_bytes()]
    elif result == 'checkpoint.names.result':
        blobs = [Path(state['inverse_name_build']['path']).read_bytes()]
    else:
        asset = 'runtime.so' if result == 'a7c2.runtime.result' else 'wbi.so'
        blobs = [(nativebuild.ROOT / 'src/fl23/assets' / asset).read_bytes()]
    names = [stage, *([request] if request else []), 'libJit.so', result]
    if result == 'a7c2.runtime.result':
        names.append('a7c2.probe.result')
    actual = {n: camera.temp_read(n, True) for n in names}
    if actual['libJit.so'] is not None:
        raise ValueError('실행 플러그인이 아직 남아 있어 카메라 쪽 작업이 끝났는지 확인할 수 없습니다. 삭제하거나 재시도하지 않습니다')
    if actual[stage] is not None and actual[stage] not in blobs:
        raise ValueError('Temporary worker bytes do not match the recorded transaction')
    if request and actual[request] not in (None, bytes.fromhex(intent['contents_hex'])):
        raise ValueError('Temporary request differs from the recorded transaction')
    executed = any(r.get('event') == 'native_intent' for r in later)
    raw = actual[result]
    metadata = {}
    if executed:
        if raw is None:
            if any(value is not None for value in actual.values()):
                raise ValueError('카메라 쪽 결과 보고서가 없어 아직 실행 결과를 확인할 수 없습니다. 임시 파일을 보존했으며 다시 쓰지 않았습니다')
            proof = completion_evidence(camera, history, root, state, intent, later)
            record('previous_temporary_namespace_empty', previous_workspace=str(root), completion_evidence=proof)
            return
        save_raw('recovered-' + result, raw)
        metadata=outcome(raw, result, intent['mode'], state, camera,
                         allow_completed_name_mount_busy=allow_completed_name_mount_busy)
        if result == 'a7c2.runtime.result' and actual['a7c2.probe.result'] is not None:
            camera.core.parse_native('probe', actual['a7c2.probe.result'])
    elif raw is not None:
        raise ValueError('Unexpected result exists before recorded execution intent')
    if camera.read('/etc/ld.so.preload', True):
        raise ValueError('Existing preload requires inspection')
    record('pending_native_outcome_accounted', previous_workspace=str(root), result=result, executed=executed, **metadata)
    # Keep the result until last: interruption during cleanup can still be
    # accounted for from that complete report on the next manual run.
    for name in [n for n in names if n != result] + [result]:
        # Recheck exact captured bytes immediately before removal.
        if camera.temp_read(name, True) != actual[name]:
            raise ValueError('Temporary state changed during recovery; no retry')
        if actual[name] is not None:
            camera.temp_remove(name)
        if camera.temp_read(name, True) is not None:
            raise ValueError('Temporary cleanup incomplete: ' + name)
    record('pending_temporary_cleanup_verified', previous_workspace=str(root))
    return metadata
