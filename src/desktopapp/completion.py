"""Human-readable completed setup; detailed evidence stays in private files."""


def archive_error(state):
    error=state.pop('error',None)
    if error:
        state.setdefault('previous_errors',[]).append(dict(phase=state.get('phase'),error=error))
        return True
    return False


def text(state):
    if state.get('phase')!='COMPLETE':raise ValueError('지난 설치가 아직 끝나지 않았습니다. "설치 계속"을 사용하세요.')
    options=state.get('options',{})
    lines=['이번에 선택한 작업이 완료되었으며 프로그램과 설정 확인을 통과했습니다.','카메라에서 미리보기, 촬영 및 재생을 확인하세요.','',
        'FL2／FL3: '+('활성화됨' if options.get('fl23',True) else '이번에 변경 안 함')]
    looks=options.get('looks',[])
    if looks:
        lines.append('커스텀 룩: 기록됨')
        lines.extend('  커스텀 %s: %s'%(row['slot'],row.get('name') or '파일 내 이름 사용') for row in looks)
    else:lines.append('커스텀 룩: 이번에 변경 안 함')
    lines.append('전자 셔터음: '+('지원을 활성화했습니다. 카메라의 오디오 신호음 설정을 확인하세요' if options.get('sound',True) else '이번에 선택 안 함'))
    if options.get('plugin'):lines.append('Lightroom 플러그인: 설치됨. 플러그인 관리자에서 활성화하세요')
    if options.get('vertical',True):
        if state.get('vertical_result',{}).get('persistent') and state['vertical_result'].get('loaded_verified'):
            lines.append('세로 촬영 정보: 켬으로 저장했고 재시작 후 동작 상태도 확인했습니다. 매번 컴퓨터에 연결해 켤 필요가 없습니다.')
        else:
            lines.extend(['세로 촬영 정보: 이전 버전은 이번 실행에서만 켜집니다.',
                '이전 작업은 부팅 기본값으로 저장되지 않았습니다. 새 버전의 "세로 표시" 페이지에서 켬으로 저장할 수 있습니다.'])
    else:lines.append('세로 촬영 정보: 이번에 변경 안 함')
    lines.extend(['','이번 설치는 끝났습니다. 결과를 보려고 다시 플래시할 필요는 없습니다.'])
    return '\n'.join(lines)


def console_summary(state):
    return {key:state[key] for key in ('schema','phase','workspace','message') if key in state} | {
        'looks':[dict(slot=r['slot'],name=r.get('name')) for r in state.get('options',{}).get('looks',[])],
        'completed_steps':state.get('completed_steps',[]),
        'physical_checks_accepted':state.get('physical_checks_accepted',False),
        'previous_error_count':len(state.get('previous_errors',[]))}
