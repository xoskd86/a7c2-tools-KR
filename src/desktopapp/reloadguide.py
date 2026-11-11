"""User-visible cold reload steps, shared by the log and completion dialog."""

STEPS = (
    '프로그램에 "읽기/쓰기가 끝났습니다"가 명확히 표시된 뒤에만 조작하세요.\n\n'
    '1. 카메라의 USB 데이터 케이블을 뽑고, 다른 외부 전원도 분리합니다.\n'
    '2. 카메라 액세스 램프가 깜박임을 멈출 때까지 기다립니다. 램프가 깜박이는 동안 배터리를 빼지 마세요.\n'
    '3. 카메라 전원 스위치를 OFF로 하고, 화면과 액세스 램프가 완전히 꺼질 때까지 기다립니다.\n'
    '4. 배터리를 빼서 배터리, USB, 기타 전원이 모두 없는 상태로 최소 1분간 기다립니다.\n'
    '5. 배터리를 다시 넣고 전원을 켭니다.'
)

WAITING_PHASES = {'WAIT_RELOAD', 'AWAITING_RELOAD', 'RESTORED_AWAITING_RELOAD',
                  'installed_pending_cold_start','WAIT_RESOURCE_RELOAD','AWAITING_RESOURCE_RELOAD'}
SOUND_PHASES = {
    'sound_on': {'written'}, 'sound_off': {'restored'},
    'sound_install': {'installed_cold_test_pending'},
    'sound_restore_sound': {'restored_cold_test_pending'},
    'sound_confirm': set(),
}


def required(action, result):
    """Use completed backend outcomes, never guess from a write button name."""
    phase = result.get('phase')
    if result.get('reload_required') is True:
        return True
    if phase in WAITING_PHASES:
        return True
    return (phase in SOUND_PHASES.get(action, set())
            and not result.get('adopted_existing_enabled'))


def sound_notice(action,result):
    """Temporary audition needs guidance while the current power stays on."""
    phase=result.get('phase')
    if action=='sound_audition' and phase=='temporary_pcm_registered_cold_reload_required_after_test':
        return dict(title='미리 듣기가 준비되었습니다. 카메라 전원을 켜 둔 상태로 유지하세요',
            status='미리 듣기가 준비되었습니다. USB를 뽑고 전원을 켠 채로 시험 촬영한 뒤, 다시 연결하면 바로 저장할 수 있습니다.',
            text='카메라 읽기/쓰기가 끝났습니다. 현재 사운드는 임시 미리 듣기용이며 아직 영구 저장되지 않았습니다.\n\n'
                 '1. USB를 뽑고 카메라 전원을 켜 둔 상태로 유지합니다. 전원을 끄거나, 배터리를 빼거나, 오디오 신호음 설정을 바꾸지 마세요.\n'
                 '2. 전자 셔터로 한 장 찍고, 소리 내용, 음량, 음높이／속도, 그리고 촬영과 재생이 정상인지 확인합니다.\n'
                 '3. USB를 다시 연결하고(대용량 저장장치) "현재 커스텀 사운드 저장"을 누릅니다. 원래 소리로 먼저 복원할 필요 없이 바로 저장할 수 있습니다.\n'
                 '4. 저장 쓰기가 끝나면 팝업 안내에 따라 전원을 완전히 끄고 다시 켜서 영구 적용되었는지 확인합니다.\n\n'
                 '"미리 듣기 정상 확인"은 미리 듣기 결과만 기록하는 선택 단계입니다.\n'
                 '이 팝업의 "확인"은 설명을 닫을 뿐이며, 미리 듣기가 정상이라고 확인한 것이 아닙니다.')
    if action=='sound_confirm' and phase in ('owner_confirmed_audio_cold_reload_required',
            'owner_audio_failed_cold_reload_required','owner_requested_gain_adjustment_cold_reload_required'):
        return dict(title='미리 듣기 결과가 기록되었습니다',status='미리 듣기 기록을 저장했습니다. 전원을 차단해 복원할 필요 없이 현재 커스텀 사운드를 바로 저장할 수 있습니다.',
                    text='이 단계는 미리 듣기 결과만 기록합니다.\n\n전원을 끄거나 복원을 기록할 필요 없이 바로 "현재 커스텀 사운드 저장"을 누르면 됩니다.'
                    '마음에 들지 않으면 구간을 조정해 다시 변환하세요. 임시 미리 듣기를 지워야 할 때는 전원 차단 단계에 따라 원래 소리로 복원하세요.')
    if action!='sound_install':return None
    if phase=='sound_conversion_required':
        return dict(title='저장이 아직 시작되지 않았습니다. 먼저 구간을 변환하세요',status='대기 중: 사운드 파일을 선택하고 변환하면 바로 저장할 수 있습니다.',
                    text='저장할 변환 구간이 없어 이번에는 카메라에 쓰지 않았습니다.\n\n오디오 또는 영상을 선택하고 시작점과 길이를 설정한 뒤, '
                    '"선택 구간 변환"을 누르세요. 변환에 성공하면 바로 "현재 커스텀 사운드 저장"을 누르면 됩니다. 미리 듣기는 선택 단계입니다.')
    return None


def text(*, setup=False, mode=None):
    if mode=='factory_prewrite_temporary':
        return ('이번 복원은 아직 쓰기를 시작하지 않았고, 연결 작업은 끝났습니다. 카메라에 같은 이름의 임시 작업 파일이 있어 '
                '파일 이름만으로는 어느 작업이 남긴 것인지 알 수 없습니다. 이번에는 덮어쓰지 않았고 복원 완료로 표시하지도 않았습니다.\n\n'
                '먼저 전원을 완전히 차단해 임시 서비스 상태를 빠져나온 뒤, 현재 카메라를 다시 읽어 복원 계획을 새로 만듭니다.\n\n'+STEPS+
                '\n\n다음 단계: USB를 다시 연결하고(대용량 저장장치), 복원 페이지의 "전원 차단 후 복원 재시도(이번에는 아직 쓰지 않음)"를 누르세요.'
                '도구가 현재 프로그램, 설정과 이름을 다시 읽고 현재 상태에서 공장 기능을 복원합니다. 이전 LUT 작업은 필요 없습니다.'
                '여전히 임시 파일 충돌이 표시되면 새 진단 패키지를 내보내세요. 카메라 파일을 직접 삭제하지 마세요.'
                '\n\n이것은 복원 쓰기 전의 재시도 단계이며, "복원 완료"나 "재시작 후 확인"이 아닙니다.')
    if mode=='factory_reset':
        return ('복원이 아직 끝나지 않았습니다: 이번 쓰기와 전체 리드백은 끝났지만, 재시작한 뒤 카메라의 실제 로드 상태를 확인해야 합니다.\n\n'+STEPS+
                '\n\n다음 단계: 카메라 화면이 정상적으로 나오는지 확인하고, USB를 다시 연결한 뒤(대용량 저장장치) 복원 페이지 위쪽의 '
                '"재시작 후 확인하고 복원 완료" 또는 아래쪽의 "재시작 후 확인하고 복원 완료(복원 쓰기 후 필수)"를 누르세요.\n\n'
                '확인을 통과해야만 복원 완료로 표시됩니다. 확인에 실패하면 로그를 보존하고, 쓰기가 끝난 것을 복원 완료로 여기지 마세요.'
                '팝업의 "확인"은 설명을 닫을 뿐이며, 확인 작업을 자동으로 완료하지 않습니다.')
    if mode=='resource_reload':
        return ('이름 단계는 끝났고 프로그램은 아직 쓰지 않았습니다. 이전 리소스를 카메라가 계속 사용 중이라 읽기 전용 마운트가 아직 복구되지 않았습니다. 먼저 이전 리소스를 해제한 뒤 남은 설치를 계속해야 합니다.\n\n'+STEPS+
            '\n\n전원을 켠 뒤 아직 촬영할 필요는 없습니다. USB를 다시 연결하고(대용량 저장장치) '+('홈의 "설치 계속"' if setup else '커스텀 룩 페이지의 "슬롯 변경 계속"')+
            '을 누르세요. 도구가 새 콜드 부팅과 읽기 전용 마운트를 확인한 뒤 남은 프로그램을 계속하며, 이름을 다시 쓰지 않고 드라이버도 재설치하지 않습니다.\n\n프로그램을 다 쓰면 새 기능을 적용하기 위한 마지막 재시작 안내가 다시 나옵니다. 확인을 누르면 설명을 닫을 뿐입니다.')
    reason = ('\n\n이번 "FL2／FL3 활성화"는 프로그램 파일에 기록되었습니다. 앞서 공장 복원 후의 재시작은 공장 상태만 로드했으므로, 이번 새 수정은 한 번 더 재시작해야 적용됩니다.'
              if mode == 'fl23_enable' else '')
    follow = ('USB를 다시 연결하고 대용량 저장장치를 선택한 뒤 홈의 "설치 계속"을 누르세요. 도구가 먼저 로드를 확인하며 다시 플래시하지 않습니다.'
              if setup else '카메라에서 FL2／FL3를 선택, 촬영, 재생할 수 있는지 확인하면 바로 사용할 수 있습니다. 다시 컴퓨터에 연결해 확인할 필요는 없으며, 문제를 살펴보려면 FL23 페이지의 선택 진단을 사용하세요.'
              if mode in ('fl23_enable', 'fl23_disable') else
              '이번 전원 차단은 임시 미리 듣기를 지우고 원래 소리로 복원하기 위한 것일 뿐, 사운드 저장의 전제 조건이 아닙니다. 전원을 켜서 원래 소리, 촬영 및 재생이 정상인지 확인한 뒤 사운드 페이지의 "전원 차단으로 원래 소리 복원됨, 복원 완료 기록"을 누를 수 있습니다. 현재 변환한 구간을 저장하는 데는 이 단계가 필요 없습니다.'
              if mode == 'sound_confirm' else
              '전원을 켠 뒤 전자 셔터로 촬영해 소리, 음량 및 촬영／재생을 확인하세요. 커스텀 사운드를 저장한 뒤에는 사운드 페이지에서 로드 상태를 확인할 수 있습니다.'
              if mode and mode.startswith('sound_') else
              '세로 표시 스위치와 프레임 선 참조를 저장했습니다. 재시작 후 먼저 가로로 들고 Fn 그리드와 오른쪽 이동 선택을 확인하고, 이어서 좌·우 세로로 각각 들고 Fn, 크리에이티브 룩 등 하위 메뉴 테두리를 확인한 뒤, 마지막으로 촬영과 재생을 확인하세요. 알려진 문제: 세로 크리에이티브 룩 선택 화면의 테두리가 여전히 두껍고, 눈금은 가로로 표시되지만 주황색 포인터 방향은 아직 수정되지 않았습니다.'
              if mode == 'vertical_enable' else
              '세로 표시를 먼저 끄고, 원래 프레임 선 참조와 리소스 인덱스를 복원했으며 보조 별칭을 제거했습니다. 현재 실행 중인 표시가 추가 이미지를 아직 참조하고 있으면 현재 표시가 깨지지 않도록 소량의 이미지 끝부분 데이터를 잠시 유지합니다. 끄기 효과에는 영향이 없으며 버튼을 다시 누를 필요도 없습니다. 재시작 후 가로／세로 표시와 촬영이 정상인지 확인하세요.'
              if mode == 'vertical_disable' else
              'ST와 수정한 슬롯 사이를 무작위로 전환해 라이브 뷰가 즉시 바뀌는지 확인한 뒤, 촬영하고 재생해 보세요. 룩만 쓴 경우 다시 컴퓨터에 연결해 확인할 필요는 없습니다. 이름 단계에서 프로그램이 아직 쓰이지 않았다고 표시되면 다시 연결해 "슬롯 변경 계속"을 누르세요.'
              if mode in ('install_all','look_import','slot_default') else
              '화면이 정상적으로 나오는지 확인하고, 메뉴／룩 미리보기를 확인한 뒤 촬영과 재생을 해 보세요. 이 페이지에 확인 대기가 표시되면 USB를 다시 연결하고 해당 페이지에 명시된 확인 버튼을 누르세요.')
    check_note='로드 확인은 상태만 읽으며 다시 플래시하지 않습니다.' if setup or (mode and mode.startswith('sound_')) else ''
    return STEPS + reason + '\n\n다음 단계: ' + follow + '\n\n' + check_note + '"확인"을 누르면 설명을 닫을 뿐이며, 자동으로 전원을 끄거나 사용자의 테스트 확인을 대신하지 않습니다.'
