"""Version-scoped FL23 planning without a firmware-provenance whitelist.

Only edited bytes have conflict guards; full readback compares bytes.
"""
from pathlib import Path
import sys
import core

WARNING='펌웨어 출처를 검증하지 않습니다. 이 저장소가 아닌 곳에서 수정한 펌웨어는 FL23과 충돌해 카메라가 정상적으로 부팅되지 않을 수 있습니다. 백업을 보관하세요.'
LEGACY_CONVERTER=bytes.fromhex('e1000035002800511f1400718800005400e4031140000039c0035fd63f2c007188020054800000100048613840000039c0035fd600030201100f1112050d13141f2003d51f2003d51f2003d51f2003d51f2003d51f2003d51f2003d51f2003d51f2003d51f2003d51f2003d51f2003d5')


def modules():
    sys.path.insert(0,str(core.BASE.parent/'looks'))
    try:
        from looktool import device_layout,firmware,nativebuild,worker
    except ImportError as exc:
        raise ValueError('LUT 호환 모드에 필요한 의존성이 없습니다. 전체 도구를 업데이트한 뒤 FL23.cmd를 다시 실행하세요.') from exc
    return device_layout,firmware,nativebuild,worker


def plan(ap,cp,version=core.VERSION,*,transitions=True):
    if version!=core.VERSION:raise ValueError('FL23은 ILCE-7CM2 2.01 전용입니다. 카메라 버전 화면을 보고 입력하세요')
    layout,firmware,_,_=modules()
    before=dict(zip(core.PROGRAMS,[ap,cp]))
    # These are instruction-boundary checks, not file-source fingerprints.
    # A LUT converter already handles native FL2/FL3 and must not be replaced.
    template=ap[0x35287f0:0x3528874].hex();templates=layout.runtime()['ap_templates']
    lut=template in templates or ap[0x3528804:0x3528874]==LEGACY_CONVERTER
    mask=templates.index(template) if template in templates else None
    manifest=core.read_json(core.BASE/'assets/patches.json')
    if lut:
        for at,expected in [(0x138814,bytes.fromhex('3720e7e7')),(0x13882e,bytes.fromhex('3820dae7'))]:
            if cp[at:at+len(expected)]!=expected:raise ValueError(f'CP FL23 호출 위치 충돌: {at:#x}; 쓰기 없음')
    after={};regions={};changed=False
    for chip,name in enumerate(core.PROGRAMS):
        source=before[name];result=bytearray(source);spans=[]
        for w in manifest[chip]['writes']:
            at=int(w['file_offset'],16);old,new=bytes.fromhex(w['before_hex']),bytes.fromhex(w['after_hex'])
            if lut and at==0x3528804:continue
            if lut and chip==1:
                native=next((entry for entry in layout.fl23_native_windows() if entry[0]==at),None)
                if native is None:continue
                current=source[at:at+len(new)]
                if current==new:continue
                if current not in (old,native[3],*layout.fl23_previous_values(at)):raise ValueError(f'CP FL23 네이티브 커브／모드 위치 충돌: {at:#x}')
                result[at:at+len(new)]=new;changed=True
                for offset in range(0,len(new),4096):spans.append((at+offset,min(4096,len(new)-offset)))
                continue
            if len(old)!=len(new) or at+len(old)>len(source):raise ValueError(f'패치 위치 범위 초과: {name} {at:#x}')
            current=source[at:at+len(old)]
            if current==new:continue
            if current!=old and not (chip==1 and current in layout.fl23_previous_values(at)):
                detail=current[:64].hex()
                raise ValueError(f'패치 위치 충돌: {name} {at:#x}; 쓰기 없음; '
                    f'현재 바이트({len(current)} bytes, first 64)={detail}. '
                    '이전 버전을 사용했다면 README에 따라 공장 기능을 먼저 복원한 뒤 설치하세요. 이번 로그는 보관하세요.')
            result[at:at+len(new)]=new;changed=True
            for offset in range(0,len(new),4096):spans.append((at+offset,min(4096,len(new)-offset)))
        if chip==1:
            from looktool import photoio,photobuild
            if photoio.present(source):
                refreshed=photoio.refresh_gamma_table(bytes(result))
                if refreshed!=result:
                    result=bytearray(refreshed);changed=True
                    spans.append((photobuild.TABLE_FILE,50*28))
        after[name]=bytes(result);regions[name]=spans
    identity={'kind':'lut' if lut else 'version-2.01','mask':mask,'provenance_checked':False,'firmware_version':version,'version_source':'user-camera-version-screen'}
    if not changed:return identity,True,before,None,None
    if not transitions:return identity,False,after,None,None
    forward=firmware.transition(before,after,regions)
    reverse=firmware.transition(after,before,regions)
    if firmware.apply(firmware.apply(before,forward),reverse)!=before:
        raise ValueError('전체 복원 후보 불일치')
    return identity,False,after,forward,reverse


def prepare(root,ap,cp,version=core.VERSION):
    identity,enabled,after,_,_=plan(ap,cp,version,transitions=False)
    result={'identity':identity,'already_enabled':enabled}
    if not enabled:
        from looktool.checkpointworker import build_programs
        result['byte_builds']=build_programs(dict(zip(core.PROGRAMS,[ap,cp])),after,root/'compat-worker',checkpoint_namespace=False)
    for n,data in after.items():(root/'candidate'/n).write_bytes(data)
    return result


def validate(root,state):
    spec=state['compatibility']
    ap,cp=[(root/'original'/(n+'.bin')).read_bytes() for n in core.PROGRAMS]
    identity,enabled,after,_,_=plan(ap,cp,state.get('version',core.VERSION),transitions=False)
    if spec['identity']!=identity or spec['already_enabled']!=enabled:
        raise ValueError('호환 작업의 원본 또는 대상이 변경되었습니다')
    for n,data in after.items():
        if (root/'candidate'/n).read_bytes()!=data:raise ValueError('호환 후보가 변경되었습니다')


def validate_enabled_colors(a,b):
    # Undo only the exact FL23 colour records, then re-merge. This proves
    # ownership without clearing calibration, user recipes or LUT tables.
    x,y=bytearray(a),bytearray(b)
    if len(x)!=8192 or len(y)!=28672:raise ValueError('색상 유닛 길이 불일치')
    for row in core.read_json(core.BASE/'assets/colors.json'):
        p,s=row['parameter'],row['slot'];x[0x1200+p]=0;y[0x196+p]=0
        x[0x1250+s*64:0x1250+(s+1)*64]=bytes(64)
        y[0x1e0+s*768:0x1e0+s*768+576]=bytes(576)
    if core.merge_colors(bytes(x),bytes(y))!=(a,b):raise ValueError('기존 FL23 색상 데이터가 불완전합니다. 재기록을 거부합니다')
