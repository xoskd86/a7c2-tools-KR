"""Compile a scoped native resource/reference plan, without device access."""
from pathlib import Path
import struct

PATHS=('/usr/share/app/image_00_32_IPSX.uxc',
       '/usr/share/app/image_01_32_IPSX.uxc','/usr/lib/appFw.so')
MAGIC=b'A7PFL001'
FORMAT='<8s6IqqQII'


def build(banks,ap,folder,*,ap_windows):
    from looktool.nativebuild import compile_worker,ROOT
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    lines=['struct PfWindow {u64 at;u32 size;const u8 *old,*value,*previous;};',
           'struct PfFile {const char *path;u64 before_size,after_size;const u8 *tail;u32 tail_size,count,retain_tail;const struct PfWindow *windows;};']
    plans=[*banks,dict(source_bytes=len(ap),target_bytes=len(ap),append_hex='',windows=ap_windows)]
    files=[]
    for number,plan in enumerate(plans):
        tail=bytes.fromhex(plan['append_hex'])
        lines.append(f'static const u8 tail_{number}[]='+cbytes(tail or b'\0')+';')
        windows=[]
        for index,row in enumerate(plan['windows']):
            before=bytes.fromhex(row['before_hex']);after=bytes.fromhex(row['after_hex'])
            if not 0<len(before)==len(after)<=4096:raise ValueError('Portrait edit size differs')
            previous=bytes.fromhex(row.get('previous_hex',row['before_hex']))
            if len(previous)!=len(before):raise ValueError('Previous portrait edit size differs')
            for label,data in [('old',before),('value',after),('previous',previous)]:
                lines.append(f'static const u8 {label}_{number}_{index}[]='+cbytes(data)+';')
            windows.append(f'{{{row["offset"]}UL,{len(before)},old_{number}_{index},value_{number}_{index},previous_{number}_{index}}}')
        lines.append(f'static const struct PfWindow windows_{number}[]='+'{'+(','.join(windows) or '{0,0,0,0,0}')+'};')
        files.append('{"'+PATHS[number]+'",'+f'{plan["source_bytes"]}UL,{plan["target_bytes"]}UL,tail_{number},{len(tail)},{len(windows)},{int(number<2)},windows_{number}'+'}')
    lines.append('static const struct PfFile pf_files[]={'+','.join(files)+'};')
    lines.append('static const u32 pf_count=3;')
    header=folder/'portrait.private.h';header.write_text('\n'.join(lines),encoding='ascii')
    path=folder/'portrait.so'
    compile_worker(ROOT/'src/vertical/native/portrait_frames.c',header,path,fingerprints=False)
    return dict(path=str(path),files=3,windows=sum(len(p['windows']) for p in plans))


def decode(raw,mode):
    if len(raw)!=struct.calcsize(FORMAT):raise ValueError('Portrait frame report length differs')
    magic,version,actual,phase,file,window,completed,status,readonly,byte_count,writes,cow=struct.unpack(FORMAT,raw)
    if magic!=MAGIC or version!=1 or actual!=mode:raise ValueError('Portrait frame report header differs')
    result=dict(mode=actual,phase=phase,file=file,window=window,completed=completed,
                status=status,readonly=readonly,bytes_written=byte_count,writes=writes,cow_ranges=cow)
    if mode==3 and phase in (1,2) and status==-11 and byte_count==writes==completed==0:
        return dict(result,cleanup_deferred=True,reload_required=True)
    busy=(mode in (1,2,3) and phase==5 and completed==3 and status==readonly==-16)
    if status and not busy:
        raise ValueError('세로 프레임 선 작업 중지: 파일 %d, 단계 %d, 영역 %d, 오류 %d; %d바이트 기록됨. 네이티브 보고서: %s'%(
            file,phase,window,status,byte_count,result))
    if mode and (phase!=5 or completed!=3):raise ValueError('Portrait frame operation did not finish: '+str(result))
    if busy:result['readonly_deferred_busy']=True
    return result


def cbytes(raw):return '{'+','.join(map(str,raw))+'}'
