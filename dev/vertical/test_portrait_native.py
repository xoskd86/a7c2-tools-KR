"""Execute the real AArch64 resource worker against an in-memory filesystem."""
from pathlib import Path
import struct
import sys

import pytest
from elftools.elf.elffile import ELFFile
from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM,UC_HOOK_INTR
from unicorn.arm64_const import UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_X2,UC_ARM64_REG_X3,UC_ARM64_REG_X4,UC_ARM64_REG_X8,UC_ARM64_REG_X30,UC_ARM64_REG_SP,UC_ARM64_REG_PC

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src/vertical'),str(ROOT/'src/looks')]
from verticaltool import imagebank,portraitbank,portraitworker

BASE=0x7f00000000
REGS=(UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_X2,UC_ARM64_REG_X3,UC_ARM64_REG_X4)


@pytest.fixture(scope='module')
def candidate(tmp_path_factory):
    plans=[];before=[];after=[]
    for group in (0,1):
        count=16;raw=bytearray(struct.pack('<8sHHI',imagebank.MAGIC,8,group,count|(count<<12)))
        raw.extend(b'\xff\xff'*count);struct.pack_into('<H',raw,16,0)
        raw.extend(struct.pack('<IIHHII',0x1a000000+group*8192,0x40000000,8,8,128,16));raw.extend(bytes(range(128)))
        target,plan=imagebank.add(bytes(raw),group,[dict(index=15,width=2,height=2,payload=b'encoded line image')])
        target,plan=portraitbank.seal(target,plan)
        plans.append(plan);before.append(bytes(raw));after.append(target)
    ap=bytes(128);target=bytearray(ap);target[32:36]=b'NEW!'
    windows=[dict(offset=32,before_hex=bytes(4).hex(),after_hex=b'NEW!'.hex())]
    build=portraitworker.build(plans,ap,tmp_path_factory.mktemp('portrait-native'),ap_windows=windows)
    return build,plans,before+[ap],after+[bytes(target)]


def execute(candidate,mode,contents=None,*,short=0,fail_window=None,fail_ro=False,shared=False):
    build,plans,before,after=candidate
    cpu=Uc(UC_ARCH_ARM64,UC_MODE_ARM);cpu.mem_map(0x10000000,0x400000)
    with Path(build['path']).open('rb') as f:
        elf=ELFFile(f)
        for segment in elf.iter_segments():
            if segment['p_type']=='PT_LOAD':cpu.mem_write(0x10000000+segment['p_vaddr'],segment.data())
        for reloc in elf.get_section_by_name('.rela.dyn').iter_relocations():
            assert reloc['r_info_type']==1027
            cpu.mem_write(0x10000000+reloc['r_offset'],struct.pack('<Q',0x10000000+reloc['r_addend']))
        entry=next(t.entry.d_val for t in elf.get_section_by_name('.dynamic').iter_tags() if t.entry.d_tag=='DT_INIT')
    cpu.mem_map(0x60000000,0x100000);cpu.mem_map(0x70000000,0x1000)
    cpu.reg_write(UC_ARM64_REG_SP,0x600f0000);cpu.reg_write(UC_ARM64_REG_X30,0x70000000)
    files={path:bytearray(data) for path,data in zip(portraitworker.PATHS,contents or before)}
    maps=(f'{BASE:x}-{BASE+0x51a7000:x} r-xp 00000000 00:01 12 /usr/lib/appFw.so\n'+
          f'{BASE+0x51b7000:x}-{BASE+0x9900000:x} rw-p 051a7000 00:01 12 /usr/lib/appFw.so\n')
    for index,path in enumerate(portraitworker.PATHS[:2]):
        address=0x30000000+index*0x10000;cpu.mem_map(address,0x10000);cpu.mem_write(address,bytes(files[path]))
        maps+=f'{address:x}-{address+0x10000:x} r--'+('s' if shared else 'p')+f' 00000000 00:01 {20+index} {path}\n'
    files.update({'/proc/self/maps':bytearray(maps.encode()),'/proc/self/cmdline':bytearray(b'/usr/bin/app\0'),
                  '/tmp/jiritsu/libJit.so':bytearray(b'worker'),
                  '/tmp/jiritsu/portrait.request':bytearray(struct.pack('<8sII',portraitworker.MAGIC,mode,0))})
    handles={};state={'ro':True,'fault_bytes':0};audit=[]

    def text(address):
        out=bytearray()
        while (c:=bytes(cpu.mem_read(address+len(out),1)))!=b'\0':out+=c
        return out.decode()

    def content(path):
        if path=='/proc/mounts':return ('/dev/nflasha15 /usr ext2 '+('ro' if state['ro'] else 'rw')+',sync 0 0\n').encode()
        return files[path]

    def memory_read(address,size):
        if address==BASE+0x5bc35b0:return struct.pack('<I',16)
        if BASE<=address<BASE+0x51a7000:return bytes(before[2][address-BASE:address-BASE+size])
        return bytes(cpu.mem_read(address,size))

    def syscall(uc,number,user):
        no=uc.reg_read(UC_ARM64_REG_X8);a=[uc.reg_read(reg) for reg in REGS];ret=0
        if no in (172,175):ret=47 if no==172 else 0
        elif no==56:
            path=text(a[1]);flags=a[2]
            if flags==0x241:files[path]=bytearray()
            elif path not in files and path not in ('/proc/mounts','/proc/self/mem'):ret=-2
            if not ret:ret=10+len(handles);handles[ret]=[path,0,flags]
        elif no==35:
            path=text(a[1]);assert path=='/tmp/jiritsu/libJit.so';files.pop(path,None)
        elif no==57:pass
        elif no in (63,67):
            path,position,flags=handles[a[0]];offset=a[3] if no==67 else position
            amount=min(a[2],short) if short else a[2]
            data=memory_read(offset,amount) if path=='/proc/self/mem' else bytes(content(path)[offset:offset+amount])
            if data:uc.mem_write(a[1],data)
            if no==63:handles[a[0]][1]+=len(data)
            ret=len(data)
        elif no in (64,68):
            path,position,flags=handles[a[0]];offset=a[3] if no==68 else position
            amount=min(a[2],short) if short else a[2];data=bytes(uc.mem_read(a[1],amount))
            if path=='/proc/self/mem':
                assert data==memory_read(offset,len(data));audit.append(('freeze',offset,len(data)))
            else:
                if path in portraitworker.PATHS:
                    assert not state['ro'];assert flags&2
                    if fail_window and path==portraitworker.PATHS[fail_window[0]] and fail_window[1]<=offset<fail_window[1]+fail_window[2]+4096:
                        remaining=fail_window[2]-state['fault_bytes']
                        if remaining<=0:ret=-5
                        else:data=data[:remaining];state['fault_bytes']+=len(data)
                    if ret!=-5:audit.append(('write',path,offset,len(data)))
                if ret!=-5:
                    if offset+len(data)>len(files[path]):files[path].extend(bytes(offset+len(data)-len(files[path])))
                    files[path][offset:offset+len(data)]=data
            if ret!=-5:ret=len(data)
            if no==64:handles[a[0]][1]+=max(ret,0)
        elif no==80:
            path=handles[a[0]][0];size=len(files[path])
            uc.mem_write(a[1],struct.pack('<QQIIIIQQqIIQ7Q',99,12,0o100644,1,0,0,0,0,size,4096,0,2048,*([0]*7)))
        elif no==40:
            assert text(a[1])=='/usr' and a[3] in (0xc30,0xc31)
            ro=bool(a[3]&1);audit.append(('mount',ro))
            if ro and fail_ro:ret=-16
            else:state['ro']=ro
        elif no==82:pass
        elif no==46:
            path=handles[a[0]][0];assert mode in (2,3) and not state['ro']
            assert files[portraitworker.PATHS[2]]==before[2]
            del files[path][a[1]:];audit.append(('truncate',path,a[1]))
        else:raise AssertionError('Unexpected syscall '+str(no))
        uc.reg_write(UC_ARM64_REG_X0,ret&((1<<64)-1))

    cpu.hook_add(UC_HOOK_INTR,syscall);cpu.emu_start(0x10000000+entry,0x70000000,count=12000000)
    assert cpu.reg_read(UC_ARM64_REG_PC)==0x70000000
    raw=bytes(files['/tmp/jiritsu/portrait.result'])
    return raw,[bytes(files[path]) for path in portraitworker.PATHS],audit,state


def test_apply_repeat_restore_and_short_io(candidate):
    raw,files,audit,state=execute(candidate,1,short=7)
    assert portraitworker.decode(raw,1)['completed']==3
    assert files==candidate[3] and state['ro']
    assert any(row[0]=='freeze' for row in audit)
    raw,again,_,_=execute(candidate,1,files)
    assert portraitworker.decode(raw,1)['bytes_written']==0 and again==files
    raw,restored,_,state=execute(candidate,2,files,short=7)
    expected=[old+new[len(old):] for old,new in zip(candidate[2][:2],candidate[3][:2])]+[candidate[2][2]]
    assert portraitworker.decode(raw,2)['completed']==3 and restored==expected and state['ro']
    # Restored indices no longer publish our images; tails stay mapped safely
    # until the old running UI has been replaced by a cold boot.
    for old,actual in zip(candidate[2][:2],restored[:2]):assert actual[:len(old)]==old


def test_mode_zero_never_changes_files_mounts_or_process_memory(candidate):
    raw,files,audit,state=execute(candidate,0)
    assert portraitworker.decode(raw,0)['status']==0 and files==candidate[2] and state['ro']
    assert not audit


def test_interrupted_metadata_write_resumes_from_exact_current_bytes(candidate):
    offset=candidate[1][0]['windows'][1]['offset']
    raw,partial,_,state=execute(candidate,1,fail_window=(0,offset,3))
    with pytest.raises(ValueError,match='작업 중지'):portraitworker.decode(raw,1)
    assert partial[2]==candidate[2][2] and state['ro']
    raw,files,_,_=execute(candidate,1,partial)
    assert portraitworker.decode(raw,1)['completed']==3 and files==candidate[3]


def test_conflict_and_shared_mapping_stop_before_file_writes(candidate):
    source=list(candidate[2]);raw=bytearray(source[0]);window=candidate[1][0]['windows'][0]
    disallowed=next(value for value in range(256) if value not in
                    (bytes.fromhex(window['before_hex'])[0],bytes.fromhex(window['after_hex'])[0]))
    raw[window['offset']]=disallowed;source[0]=bytes(raw)
    report,files,audit,_=execute(candidate,1,source)
    with pytest.raises(ValueError):portraitworker.decode(report,1)
    assert files==source and not any(row[0]=='write' for row in audit)
    report,files,audit,_=execute(candidate,1,shared=True)
    with pytest.raises(ValueError):portraitworker.decode(report,1)
    assert files==candidate[2] and not any(row[0]=='write' for row in audit)


def test_only_completed_readonly_busy_is_a_reload_result(candidate):
    raw,files,_,_=execute(candidate,1,fail_ro=True)
    result=portraitworker.decode(raw,1)
    assert result['readonly_deferred_busy'] and files==candidate[3]


def test_tail_cleanup_requires_original_disk_and_loaded_references(candidate):
    raw,unchanged,audit,_=execute(candidate,3,candidate[3])
    assert portraitworker.decode(raw,3)['cleanup_deferred']
    assert unchanged==candidate[3] and not any(row[0]=='write' for row in audit)
    _,restored,_,_=execute(candidate,2,candidate[3])
    raw,clean,_,_=execute(candidate,3,restored)
    assert portraitworker.decode(raw,3)['completed']==3 and clean==candidate[2]
