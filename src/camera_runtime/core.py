"""Portable, offline validation and per-body planning. No USB imports here."""
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import sys

BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE/'vendor'))
MODEL='ILCE-7CM2'
VERSION='2.01'
ORIGINAL=['2ea02f7ecbc5bb917316fe8ef4ce5ffd87eb66efaae78ba2ce11420552df539b',
          'f5a477588698ef965748903b8959a39ce4f59ffd4aee4d4234ef1670f56a1428']
FINAL=['1aaeb8fbe0297950cab454d498d1a5ba874f91542aa2737aae27a73971e324d7',
       '442c9261d7c76c30f65e5b66ae133fea40fc31ee6c0046ff78b6b45df8ab455c']
PROGRAMS=['appFw.so','cpapp-b.bin']
CP_KEYS=[0x02a40001,0x02a4000d]
AP_KEYS=[0x02cf1287,0x02cf1289]
COLOUR_SLOTS=[(55,23),(56,25)]
NATIVE_PATHS={'libJit.so','a7c2.install.request','a7c2.install.result','a7c2.probe.result',
              'a7c2.wbi.request','a7c2.wbi.result','a7c2.runtime.result','a7c2-preview-staged.so'}

def sha(data):return hashlib.sha256(data).hexdigest()
def read_json(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def save_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    with temp.open('w',encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,indent=2);f.flush()
        __import__('os').fsync(f.fileno())
    temp.replace(path)

def assets():
    manifest=read_json(BASE/'assets/manifest.json')
    names={'installer.so','probe.so','wbi.so','runtime.so','patches.json','colors.json'}
    if not names.issubset(manifest['files']):raise ValueError('패키지 에셋 목록 불일치')
    for name in manifest['files']:
        if not (BASE/'assets'/name).is_file():raise ValueError('패키지에 파일이 없습니다: '+name)
    return manifest

def backup_records(raw):
    from pmca.backup import BackupFile
    result=[]
    for m in re.finditer(b'BK4\0',raw):
        try:b=BackupFile(io.BytesIO(raw[m.start()-12:]))
        except Exception:continue
        result.append(dict(b.listProperties()))
    if len(result)!=2:raise ValueError('카메라 백업에는 검증이 유효한 BK4 두 개가 있어야 합니다')
    return result

def patch_program(original,chip,reverse=False):
    data=read_json(BASE/'assets/patches.json')[chip]
    # No firmware-provenance whitelist: guard only bytes we actually replace.
    spans=[]
    for w in data['writes']:
        offset=int(w['file_offset'],16)
        before=bytes.fromhex(w['after_hex'] if reverse else w['before_hex'])
        after=bytes.fromhex(w['before_hex'] if reverse else w['after_hex'])
        if (not before or len(before)!=len(after) or offset<0 or offset+len(before)>len(original)
            or original[offset:offset+len(before)]!=before):raise ValueError('패치 영역 전제 조건 불일치')
        spans.append((offset,before,after))
    spans.sort()
    if any(a[0]+len(a[1])>b[0] for a,b in zip(spans,spans[1:])):raise ValueError('패치 영역이 겹칩니다')
    result=bytearray(original)
    for offset,before,after in spans:result[offset:offset+len(before)]=after
    if len(result)!=len(original):raise ValueError('후보 파일 길이가 변경되었습니다')
    return bytes(result)


def merge_colors(unit01,unit0d):
    if len(unit01)!=8192 or len(unit0d)!=28672:raise ValueError('색상 유닛 길이 불일치')
    a,b=bytearray(unit01),bytearray(unit0d)
    for row in read_json(BASE/'assets/colors.json'):
        parameter,slot=row['parameter'],row['slot']
        if (parameter,slot) not in COLOUR_SLOTS:raise ValueError('색상 매핑 불일치')
        if unit01[0x1200+parameter] or unit0d[0x196+parameter]:raise ValueError('대상 파라미터가 이미 사용 중이어서 덮어쓰기를 거부합니다')
        if slot in unit01[0x1200:0x1250] or slot in unit0d[0x196:0x196+74]:raise ValueError('대상 색상 슬롯을 다른 파라미터가 이미 참조하고 있습니다')
        c=0x1250+slot*64;m=0x1e0+slot*768
        if any(unit01[c:c+64]) or any(unit0d[m:m+576]):raise ValueError('대상 색상 슬롯에 이미 데이터가 있습니다')
        chroma,matrix=colour_record(row)
        a[0x1200+parameter]=slot;b[0x196+parameter]=slot
        a[c:c+64]=chroma;b[m:m+576]=matrix
    # Check every existing look's mapping and complete referenced records.
    for p in range(55):
        c=unit01[0x1200+p];m=unit0d[0x196+p]
        if a[0x1200+p]!=c or b[0x196+p]!=m:raise ValueError('기존 스타일 매핑이 변경되었습니다')
        if a[0x1250+c*64:0x1250+c*64+64]!=unit01[0x1250+c*64:0x1250+c*64+64]:raise ValueError('기존 색상 기록이 변경되었습니다')
        if m<32 and b[0x1e0+m*768:0x1e0+m*768+576]!=unit0d[0x1e0+m*768:0x1e0+m*768+576]:raise ValueError('기존 행렬이 변경되었습니다')
    return bytes(a),bytes(b)

def runtime_counter(chip,key,a,b):
    if chip or a.attr!=b.attr:return False
    if key==0x01070000 and len(a.data)==len(b.data)==8:
        return 0<=int.from_bytes(b.data,'little')-int.from_bytes(a.data,'little')<=1000000
    if key==0x02780000 and len(a.data)==len(b.data)==256:
        delta=int.from_bytes(b.data[24:28],'little')-int.from_bytes(a.data[24:28],'little')
        return a.data[:24]==b.data[:24] and a.data[28:]==b.data[28:] and 0<delta<=1000000
    return False

def verify_preservation(before,after,expected):
    kept=0;telemetry=[]
    for chip in [0,1]:
        if set(before[chip])!=set(after[chip]):raise ValueError('설정 항목 집합이 변경되었습니다')
        for key,a in before[chip].items():
            b=after[chip][key]
            if (chip,key) in expected:
                if b.data!=expected[chip,key] or b.attr!=a.attr:raise ValueError('대상 설정 리드백 불일치')
            elif (a.data,a.attr)==(b.data,b.attr):kept+=1
            elif runtime_counter(chip,key,a,b):telemetry.append(hex(key))
            else:raise ValueError(f'추가 설정 변경 발견: chip{chip}/{key:#x}. 기록을 보존한 뒤 확인하세요')
    return {'preserved_properties':kept,'runtime_counters':telemetry}

def parse_native(name,raw,mode=None):
    if name=='installer':
        if len(raw)!=240:raise ValueError('설치기 결과 길이 불일치')
        v=struct.unpack_from('<8sIIIIqqqqqqqQQQQ',raw)
        if v[:3]!=(b'A7IRP001',1,mode) or v[3]!=9 or any(v[i] for i in [5,6,7,8,9,10]):
            raise ValueError('설치기가 완전히 성공하지 못했습니다. 재시도하거나 재부팅하지 마세요')
        before=[raw[112:144].hex(),raw[144:176].hex()]
        after=[raw[176:208].hex(),raw[208:240].hex()]
        if before!=(FINAL if mode==2 else ORIGINAL):raise ValueError('쓰기 전 전체 파일 해시 불일치')
        if mode and after!=(ORIGINAL if mode==2 else FINAL):raise ValueError('쓰기 후 전체 파일 해시 불일치')
        if mode==0 and v[4]!=0:raise ValueError('검증 전용 모드에서 0이 아닌 쓰기가 발생했습니다')
        return {'mode':mode,'window_writes':v[4],'before':before,'after':after,'readonly_verified':True}
    if name=='probe':
        if len(raw)<72:raise ValueError('진단 결과가 너무 짧습니다')
        v=struct.unpack_from('<8sIIqqqqQIIII',raw)
        if v[:3]!=(b'A7PRB001',1,16) or any(v[i] for i in [3,4,6]) or len(raw)!=72+sum(v[8:11]):
            raise ValueError('모델 필드, 권한 또는 진단 결과 불일치')
        mounts=raw[72+v[8]:72+v[8]+v[9]]
        usr=[line.split() for line in mounts.splitlines() if len(line.split())>=4 and line.split()[1]==b'/usr']
        readonly=(b'ro' in usr[0][3].split(b',')) if len(usr)==1 and usr[0][0]==b'/dev/nflasha15' and usr[0][2]==b'ext2' else None
        return {'model_index':v[2],'product_column':9,'root_verified':True,'usr_mount_readonly':readonly}
    if name=='wbi':
        if len(raw)!=192:raise ValueError('부팅 스냅샷 결과 길이 불일치')
        v=struct.unpack('<8sII9q16s16s32s32sII',raw)
        if v[:3]!=(b'A7WBR001',1,mode) or any(v[i] for i in [3,4,5]):raise ValueError('부팅 스냅샷 처리가 검증되지 않았습니다. 반복 실행하지 마세요')
        if v[12].rstrip(b'\0')!=b'2\n' or v[14].rstrip(b'\0')!=b'*wbi4\n':raise ValueError('지원하지 않는 부팅 스냅샷 초기 상태입니다')
        if mode and (v[7]!=1 or v[9]!=1 or any(v[i] for i in [8,10,11]) or not v[16]
                     or v[13].rstrip(b'\0')!=b'0\n' or v[15].rstrip(b'\0')!=b'*wbi4\n'):
            raise ValueError('부팅 스냅샷 쓰기 또는 리드백이 불완전합니다')
        return {'mode':mode,'verified':True,'fresh_drop_log':bool(v[16])}
    if name=='runtime':
        if len(raw)!=1832:raise ValueError('런타임 결과 길이 불일치')
        v=struct.unpack_from('<8sIIqqqQII',raw)
        if v[:3]!=(b'A7RUN001',1,6) or v[3] or v[4] or v[7]!=16:raise ValueError('런타임 식별자／결과 불일치')
        spans=[]
        for i,(offset,n) in enumerate([(0x3528804,112),(0x4b2ed19,2),(0x59892b8,40),(0x5989370,40),(0x4b4a5e4,12),(0x4b3b544,12)]):
            spans.append({'offset':offset,'length':n,'memory':raw[56+i*256:56+i*256+n].hex(),
                          'disk':raw[56+i*256+128:56+i*256+128+n].hex()})
        return {'spans':spans,'image_mode':raw[1592:1608].split(b'\0')[0].decode('ascii','replace'),
                'boot_id':raw[1768:1832].split(b'\0')[0].decode('ascii','replace')}
    raise ValueError('알 수 없는 네이티브 모듈')

def slot_row_sums(block):
    """12B 슬롯의 6개 계수를 3x3으로 전개한 뒤의 세 행 합(중성 입력에서 색이 치우치지 않으면 모두 1024여야 함)."""
    a,b,c,d,e,f=struct.unpack('<6h',block)
    return (a+b+1024-a-b, -c+c+d+1024-d, -e-f+e+f+1024)

def colour_record(row):
    """색상 기록 검사: 길이, 색도 끝 8개 항목이 0인지, 48개 슬롯의 전개 행 합, 공여체 목표와의 일치.

    576B 행렬 기록 = B／A1／A2 세 개의 192B 끝점, 끝점마다 16슬롯 × 12B 6계수.
    0.4.x 이전에는 각 끝점의 16슬롯을 같은 계수로 채웠지만, 실기 네이티브 룩의 16슬롯은 서로
    다르다(같은 bank, 같은 끝점 안에 15～16개의 서로 다른 값). 0x780F도 이 16슬롯 단위로
    슬롯별 패킹되므로 0.5.0부터 공여체 값을 슬롯별로 복사한다(설명은 git 이력의
    커밋 05f3cd2 의 src/fl23/docs/fl23-v050-gamma-and-perslot-matrix.md 참조).

    여기서는 더 이상 끝점 내부의 통일을 요구하지 않고 대신: 각 슬롯의 전개 행 합은 1024여야 하며(구조
    검사), 전체 기록은 assets/fx2_fl23_donor.json 의 목표값과 바이트 단위로 같아야 한다
    (내용 검사). 후자는 이 도구의 일관된 엄격한 원칙을 따른다: "일부 슬롯은 새것, 일부
    슬롯은 옛것"이나 공여체로 뒷받침되지 않은 어떤 값도 설치 과정에 들이지 않는다.
    """
    chroma=bytes.fromhex(row['chroma']);matrix=bytes.fromhex(row['matrix'])
    if len(chroma)!=64 or len(matrix)!=576:raise ValueError('색상 기록 길이 불일치')
    if any(chroma[48:]):raise ValueError('색상 기록의 색도 끝 8개 항목이 0이 아닙니다')
    for index in range(0,576,12):
        if any(s!=1024 for s in slot_row_sums(matrix[index:index+12])):
            raise ValueError(f'{index//12}번째 슬롯의 전개 행 합이 1024가 아니어서 쓰기를 거부합니다')
    for look in read_json(BASE/'assets/fx2_fl23_donor.json')['looks']:
        if look['target_parameter']!=row['parameter'] or look['target_slot']!=row['slot']:
            continue
        want_chroma=b''.join(struct.pack('<8h',*group) for group in look['cc1_int16'])+bytes(16)
        want_matrix=b''.join(struct.pack('<6h',*ab)
                             for name in ('B','A1','A2') for ab in look['matrix_slots'][name])
        if chroma!=want_chroma:raise ValueError('색도가 공여체 목표값과 다릅니다')
        if matrix!=want_matrix:raise ValueError('행렬이 공여체 목표값과 다릅니다(슬롯별)')
        return chroma,matrix
    raise ValueError('알 수 없는 색상 파라미터／슬롯 조합이어서 쓰기를 거부합니다')

def colour_write_spans():
    """merge_colors가 덮어쓰는 바이트 범위를 CP 유닛 인덱스별로 묶은 것(반열린 구간).

    인덱스 0은 unit-02a40001(8192B), 1은 unit-02a4000d(28672B)에 해당한다.
    복원 과정에서 "이 도구의 어떤 버전이 쓴 색상"과 "출처를 알 수 없는 내용"을 구분하기 위한 용도:
    영역 밖이 원본과 같기만 하면, 영역 안은 어느 버전이든 안전하게 원본으로 되돌려 쓸 수 있다.
    """
    spans={0:[],1:[]}
    for parameter,slot in COLOUR_SLOTS:
        spans[0].append((0x1200+parameter,0x1201+parameter))
        spans[0].append((0x1250+slot*64,0x1250+slot*64+64))
        spans[1].append((0x196+parameter,0x197+parameter))
        spans[1].append((0x1e0+slot*768,0x1e0+slot*768+576))
    merged={}
    for index,regions in spans.items():
        out=[]
        for start,end in sorted(regions):
            if out and start<=out[-1][1]:out[-1]=(out[-1][0],max(out[-1][1],end))
            else:out.append((start,end))
        merged[index]=out
    return merged

def matches_outside_colour(current,original,index):
    """current가 색상 쓰기 영역 밖에서 original과 완전히 같은지 여부."""
    if len(current)!=len(original):return False
    cursor=0
    for start,end in colour_write_spans()[index]:
        if current[cursor:start]!=original[cursor:start]:return False
        cursor=max(cursor,end)
    return current[cursor:]==original[cursor:]

def colour_window_diff(current,original,index):
    """색상 쓰기 영역 밖에서 처음 나타나는 차이의 오프셋. 없으면 None 반환."""
    if len(current)!=len(original):return 'len'
    skip=[]
    for start,end in colour_write_spans()[index]:skip.append((start,end))
    for offset in range(len(current)):
        if current[offset]==original[offset]:continue
        if any(start<=offset<end for start,end in skip):continue
        return offset
    return None
