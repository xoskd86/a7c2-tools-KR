"""Compose selected imports and ST resets without unrelated feature toggles."""


def options(rows):
    from looktool import bundle
    if not rows:
        raise ValueError('작업 항목을 하나 이상 추가하세요. 추가하지 않은 슬롯은 그대로 유지됩니다.')
    writes,resets,operations,seen=[],[],[],set()
    for row in rows:
        slot=row.get('slot');action=row.get('action','write')
        if type(slot) is not int or slot not in range(1,7) or slot in seen:
            raise ValueError('대상 슬롯마다 작업은 하나만 가능하며, 범위는 1–6입니다.')
        seen.add(slot)
        if action=='restore':
            resets.append(slot);operations.append(dict(slot=slot,action=action))
        elif action=='write':
            path=row.get('path') or row.get('file')
            if not path:raise ValueError('커스텀 %s에 사용할 A7Look 파일을 선택하세요.'%slot)
            name=row.get('name') or ''
            if not isinstance(name,str):raise ValueError('룩 이름은 텍스트여야 합니다.')
            value=dict(slot=slot,path=path,name=name.strip())
            writes.append(value);operations.append(dict(value,action=action))
        else:raise ValueError('Unknown slot operation: '+str(action))
    result, _ = bundle.documents(dict(looks=writes,reset_slots=resets,slot_operations=operations,
        fl23=False,sound=False,vertical_persistent=False))
    resolved={r['slot']:r for r in result['looks']}
    result['slot_operations']=[dict(resolved[r['slot']],action='write') if r['action']=='write' else r for r in operations]
    return result


def read_only_failure(state):
    """Only retry a failure with evidence that persistent writing never began."""
    if state.get('phase')!='NEEDS_REVIEW' or state.get('failed_during') not in ('READING','PLANNED','PREFLIGHT_VERIFIED'):
        return False
    events=state.get('journal',[])
    if any(e.get('event')=='native_intent' and e.get('mode') in (1,2,3) for e in events):return False
    stages=[i for i,e in enumerate(events) if e.get('event')=='native_stage_intent']
    for n,index in enumerate(stages):
        stop=stages[n+1] if n+1<len(stages) else len(events)
        if not any(e.get('event')=='native_cleanup_verified' for e in events[index+1:stop]):return False
    return True
