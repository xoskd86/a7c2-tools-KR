"""Guided combined persistent install, cold reload and loaded-state verification.

All device work goes through existing explicit worker actions. A failed or
unknown installation is never replayed. Physical power removal remains manual.
"""
import json
import uuid
from pathlib import Path
from .paths import data_root, write_json
from . import lookbatch


def run(arguments, *, dispatch):
    from looktool import bundle, settingsresume
    from looktool.power import require_power
    require_power(confirmed=arguments.get('power_confirmed', False))
    if arguments.get('firmware') != '2.01':
        raise ValueError('One-click setup supports ILCE-7CM2 2.01')
    root = Path(arguments['workspace']).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'setup.private.json'
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    if state and state['phase'] == 'COMPLETE':
        from .completion import archive_error
        if archive_error(state):write_json(path,state)
        print('이번 원클릭 설치는 이미 끝났습니다. 중복 쓰기는 하지 않았습니다.', flush=True)
        return state
    if state is None:
        from .presets import resolve
        inputs = root / 'inputs'
        inputs.mkdir(exist_ok=True)
        options, _ = bundle.documents(resolve(arguments.get('options', {})), inputs)
        # Version the behavior so already-written older jobs keep their plan.
        options['vertical_persistent'] = bool(options.get('vertical', True))
        options['portrait_frames'] = bool(options.get('vertical', True))
        if options.get('plugin'):
            from .exiftool import ensure
            options['exiftool']=ensure(options.get('exiftool'))
        state = dict(schema='a7c2-one-click/1', phase='READY', workspace=str(root), options=options,
                     firmware='2.01', completed_steps=[], physical_checks_accepted=False)
        write_json(path, state)

    def save(phase, **values):
        if phase!='NEEDS_REVIEW' and 'error' not in values:
            from .completion import archive_error
            archive_error(state)
        state.update(values, phase=phase)
        write_json(path, state)
        print('원클릭 설치: ' + phase, flush=True)

    def call(action, values, name):
        step = root / 'steps' / name
        step.mkdir(parents=True, exist_ok=True)
        print('실행: ' + action, flush=True)
        return dispatch(action, values, step)

    options = state['options']
    common = dict(firmware='2.01', power_confirmed=True)
    install = Path(state.get('install_workspace') or root / 'install')
    if state['phase']=='NEEDS_REVIEW' and state.get('verify_result',{}).get('phase') in ('LOADED','COMPLETE'):
        # Older packages called a computer-only plugin failure NEEDS_REVIEW.
        # Once camera work was verified, resume only the remaining host steps.
        done=state.get('completed_steps',[])
        child=install/'direct.private.json'
        verified=state['verify_result'].get('workspace')
        if ('persistent_features' in done and (not options.get('sound',True) or 'sound' in done)
                and (not options.get('vertical',True) or options.get('vertical_persistent'))
                and verified and Path(verified).resolve()==install.resolve() and child.is_file()
                and json.loads(child.read_text(encoding='utf-8')).get('phase') in ('LOADED','COMPLETE')):
            print('카메라 설치와 재시작 확인을 통과했습니다. 컴퓨터 쪽 플러그인 단계만 계속합니다.',flush=True)
            save('FINALIZING')
    if state['phase']=='PREPARING_VERTICAL' or (state['phase']=='NEEDS_REVIEW' and state.get('failed_step')=='portrait_frames'):
        print('현재 세로 표시와 프레임 선 상태를 다시 읽어 이번 설치를 이어 갑니다. 같은 내용은 다시 쓰지 않습니다.',flush=True)
        save('PREPARED')
    if state['phase'] in ('INSTALLING', 'NEEDS_REVIEW', 'RESUMING_RESOURCES','RESUMING_SETTINGS'):
        child = install / 'direct.private.json'
        child_state = json.loads(child.read_text(encoding='utf-8')) if child.exists() else {}
        # A ready resource checkpoint can start its program child before the
        # parent receives the returned workspace. Recover that exact child.
        nested=install/'program-install/direct.private.json'
        if child_state.get('phase')=='RESOURCE_RELOAD_VERIFIED' and nested.is_file():
            install=nested.parent
            child_state=json.loads(nested.read_text(encoding='utf-8'))
            state['install_workspace']=str(install)
        if (state['phase'] in ('RESUMING_SETTINGS','NEEDS_REVIEW') and state.get('resume_settings_from') and not child_state and
                (not install.exists() or not any(install.iterdir()))):
            install=Path(state['resume_settings_from'])
            save('SETTINGS_REVIEW_REQUIRED',install_workspace=str(install))
        elif (state['phase']=='RESUMING_RESOURCES' and not child_state and
                (not install.exists() or not any(install.iterdir()))):
            # Dispatch never created a transaction; continue from the saved
            # resource source in a fresh child, never reuse an unknown job.
            install=Path(state['resume_resource_from'])
            save('WAIT_RESOURCE_RELOAD',install_workspace=str(install))
        elif child_state.get('phase') in ('AWAITING_RESOURCE_RELOAD','RESOURCE_RELOAD_VERIFIED'):
            save('WAIT_RESOURCE_RELOAD')
        elif child_state.get('phase') in ('AWAITING_RELOAD', 'LOADED', 'COMPLETE'):
            # Re-read device proof even when the child finished before the
            # parent saved its result. LOADED children need no further reboot.
            save('WAIT_RELOAD')
        elif (child_state.get('schema')=='a7c2-direct-job/1' and child_state.get('mode')=='install_all'
              and not child_state.get('settings_resume_from')
              and isinstance(child_state.get('journal'),list)
              and lookbatch.read_only_failure(child_state)):
            # A version/window conflict can occur while building the host plan,
            # before any camera write. A fixed tool must allow a fresh plan,
            # preserving the failed child and its saved selection.
            print('지난 설치는 쓰기 전에 중지되었습니다. 실패 기록을 보존하고 새 버전 코드로 계획을 다시 만들며, 드라이버는 재설치하지 않습니다.',flush=True)
            state.setdefault('failed_install_workspaces',[]).append(str(install))
            install=root/('install-retry-'+uuid.uuid4().hex[:8])
            save('PREPARED',install_workspace=str(install))
        elif settingsresume.eligible(child_state):
            save('SETTINGS_REVIEW_REQUIRED')
        else:
            from . import reviewnames
            try:reviewnames.source(install)
            except (ValueError,OSError,KeyError):
                from looktool import namespause
                try:namespause.source(install)
                except (ValueError,OSError,KeyError):
                    detail='; 원래 단계: %s; 원래 오류: %s'%(child_state.get('failed_during',child_state.get('phase','알 수 없음')),
                        child_state.get('error','원래 오류가 없습니다. 하위 트랜잭션을 포함한 새 진단 패키지를 내보내세요'))
                    raise ValueError('지난 설치 결과를 확인해야 하며, 다시 플래시하지 않습니다. 이 폴더를 보존하고 전체 오류를 확인하세요: ' + str(root)+detail)
                save('RESOURCE_REVIEW_REQUIRED')
            else:
                print('지난번에 이름을 기록하기 전에 공간이 부족했습니다. 먼저 현재 바이트를 확인하고, 그때 완료된 임시 보고서를 정리합니다.',flush=True)
                reviewed=call('review_failed_names',dict(workspace=str(install)),'review-'+uuid.uuid4().hex[:6])
                if not reviewed.get('pre_program_failure_accounted'):raise ValueError('Name failure review did not finish; no retry')
                state.setdefault('failed_install_workspaces',[]).append(str(install))
                install=root/('install-retry-'+uuid.uuid4().hex[:8])
                save('PREPARED',install_workspace=str(install),last_name_failure_review=reviewed)
    host_step=False
    try:
        if state['phase']=='SETTINGS_REVIEW_REQUIRED':
            previous=install
            failed=state.setdefault('failed_install_workspaces',[])
            if str(previous) not in failed:failed.append(str(previous))
            candidate=root/('install-settings-'+uuid.uuid4().hex[:8])
            save('RESUMING_SETTINGS',resume_settings_from=str(previous),install_workspace=str(candidate))
            print('지난번 프로그램과 이름은 전체 리드백까지 끝났습니다. 현재 내용을 확인한 뒤 설정만 계속하며, 드라이버 재설치나 프로그램 재플래시는 하지 않습니다.',flush=True)
            result=call('direct_resume_settings',dict(common,workspace=str(candidate),failed_workspace=str(previous)),
                        'settings-resume-'+uuid.uuid4().hex[:6])
            install=Path(result['workspace']);state['install_result']=result
            if result['phase']!='AWAITING_RELOAD':raise ValueError('Settings continuation did not reach reload preparation')
            save('WAIT_RELOAD')
            from .reloadguide import text
            print('모든 읽기/쓰기가 끝났습니다.\n'+text(setup=True),flush=True)
            return state
        if state['phase'] in ('WAIT_RESOURCE_RELOAD','RESOURCE_REVIEW_REQUIRED'):
            print('이름은 이미 기록되었습니다. 리소스 재로드를 확인／준비하며, 이름을 다시 쓰거나 드라이버를 재설치하지 않습니다.',flush=True)
            previous=install
            candidate=root/('install-continue-'+uuid.uuid4().hex[:8])
            save('RESUMING_RESOURCES',resume_resource_from=str(previous),install_workspace=str(candidate))
            result=call('direct_resume_looks',dict(common,workspace=str(candidate),failed_workspace=str(previous)),
                        'resource-resume-'+uuid.uuid4().hex[:6])
            if result['phase']=='RESOURCE_RELOAD_VERIFIED':
                previous=Path(result['workspace'])
                candidate=root/('install-after-resources-'+uuid.uuid4().hex[:8])
                save('RESUMING_RESOURCES',resume_resource_from=str(previous),install_workspace=str(candidate))
                result=call('direct_resume_looks',dict(common,workspace=str(candidate),failed_workspace=str(previous)),
                            'resource-continue-'+uuid.uuid4().hex[:6])
            install=Path(result['workspace']);state['install_workspace']=str(install)
            if result['phase']=='AWAITING_RESOURCE_RELOAD':
                # A names-only preparation returns a checkpoint; never treat
                # it as a completed program install or enable other features.
                save('WAIT_RESOURCE_RELOAD',resource_result=result)
                from .reloadguide import text
                print('모든 읽기/쓰기가 끝났습니다.\n'+text(setup=True,mode='resource_reload'),flush=True)
                return state
            state['install_result']=result
            state['completed_steps'].append('persistent_features')
            if result['phase']=='AWAITING_RELOAD':
                save('WAIT_RELOAD');return state
            save('FINALIZING')
        if state['phase'] == 'READY':
            save('DRIVERS')
            result = call('usb_prepare', {}, 'drivers')
            state['driver_result'] = result
            state['completed_steps'].append('drivers')
            save('PREPARED')
        if state['phase'] == 'DRIVERS':
            print('지난번에는 연결 준비 단계에서 멈췄습니다. 이번에는 연결만 테스트하고 드라이버는 다시 바꾸지 않습니다.', flush=True)
            state['driver_result'] = call('connect', {}, 'driver-recheck')
            state['completed_steps'].append('drivers')
            save('PREPARED')
        if state['phase'] == 'PREPARED':
            if options.get('portrait_frames') and not state.get('portrait_frames_preinstalled'):
                save('PREPARING_VERTICAL',failed_step='portrait_frames')
                result=call('vertical_enable',dict(common,workspace=str(root/'vertical'),drivers_prepared=True,combined=True),
                            'vertical-frames-'+uuid.uuid4().hex[:6])
                if result.get('phase') not in ('AWAITING_RELOAD','LOADED','COMPLETE'):
                    raise ValueError('세로 프레임 선 단계가 기록 완료／목표 상태 일치를 반환하지 않았습니다. 로그를 보존하세요')
                state['portrait_frames_preinstalled']=True
                state['portrait_frames_result']=result
                state.pop('failed_step',None)
                save('PREPARED')
            save('INSTALLING')
            result = call('direct_install_all', dict(common, workspace=str(install), options=options), 'install')
            state['install_result'] = result
            if result['phase']=='AWAITING_RESOURCE_RELOAD':
                save('WAIT_RESOURCE_RELOAD')
                from .reloadguide import text
                print('모든 읽기/쓰기가 끝났습니다.\n'+text(setup=True,mode='resource_reload'),flush=True)
                return state
            state['completed_steps'].append('persistent_features')
            if result['phase'] == 'AWAITING_RELOAD':
                save('WAIT_RELOAD')
                from .reloadguide import text
                print('이번 프로그램／룩 쓰기와 전체 리드백이 끝났습니다.\n' + text(setup=True), flush=True)
                return state
            save('FINALIZING')
        if state['phase'] == 'WAIT_RELOAD':
            print('저장된 설치 계획을 이어 갑니다. 먼저 새 부팅을 확인하며 다시 플래시하지 않습니다.', flush=True)
            try:
                result = call('direct_verify', dict(workspace=str(install), accepted=False), 'verify')
            except ValueError as exc:
                if '콜드 부팅' in str(exc):
                    save('WAIT_RELOAD', message=str(exc))
                    print(str(exc), flush=True)
                    return state
                raise
            state['verify_result'] = result
            if 'persistent_features' not in state['completed_steps']:
                state['completed_steps'].append('persistent_features')
            save('FINALIZING')
        if state['phase'] == 'FINALIZING':
            if options.get('sound',True) and 'sound' not in state['completed_steps']:
                sound=root/'sound'
                state['sound_result']=call('sound_on',dict(common,workspace=str(sound),backup=str(root/'sound-backups')),'sound-adoption')
                state['sound_workspace']=str(sound)
                state['sound_backup']=str(root/'sound-backups')
                state['completed_steps'].append('sound')
                save('FINALIZING')
            if options.get('plugin') and 'plugin' not in state['completed_steps']:
                host_step=True
                state['plugin_result'] = call('plugin_install', {'exiftool': options.get('exiftool')}, 'plugin')
                child = json.loads((install / 'direct.private.json').read_text(encoding='utf-8'))
                _, rows = bundle.documents(child['bundle_options'])
                if rows:
                    from looktool.rawidentity import activated
                    state['raw_profile_result'] = activated(
                        [dict(r['document'], name=r['name']) for r in rows], [r['slot'] for r in rows],
                        child['job_id'], child['raw_profile_effective_since'])
                state['completed_steps'].append('plugin')
                save('FINALIZING')
                host_step=False
            if options.get('vertical', True) and 'vertical' not in state['completed_steps']:
                if options.get('vertical_persistent'):
                    result = state.get('verify_result', state.get('install_result', {})).get('vertical_result', {})
                    if not (result.get('persistent') and result.get('loaded_verified') and
                            result.get('saved') == result.get('running') == 'ON' and result.get('alias_count') == 3):
                        raise ValueError('세로 표시의 영구 저장 로드가 아직 확인되지 않았습니다. 세션 임시 활성화로 완료를 대신하지 않습니다. 로그를 보존하세요.')
                    state['vertical_result'] = result
                else:
                    # Resume old plans without silently adding a new flash write.
                    state['vertical_result'] = call('vertical_enable', dict(common, workspace=str(root / 'vertical'), session_only=True), 'vertical')
                state['completed_steps'].append('vertical')
            message='선택한 설치가 완료되었습니다. 카메라에서 미리보기, 이름, 촬영 및 재생을 확인하세요.'
            if options.get('vertical',True):
                message+=('세로 표시를 켬으로 저장했고, 재시작 후 실제 동작 상태도 확인했습니다.' if options.get('vertical_persistent') else
                    '이전 설치의 세로 표시는 이번 부팅에서만 켜집니다. 새 버전의 세로 표시 페이지에서 켬으로 저장할 수 있습니다.')
            save('COMPLETE', message=message)
            print(state['message'], flush=True)
        return state
    except BaseException as exc:
        if state['phase']=='FINALIZING' and host_step:
            save('FINALIZING',error=str(exc))
        elif state['phase'] not in ('DRIVERS', 'WAIT_RELOAD'):
            save('NEEDS_REVIEW', error=str(exc))
        else:
            save(state['phase'], error=str(exc))
        raise
