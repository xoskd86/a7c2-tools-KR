"""Published helper/curve migration, no USB or complete-firmware hash gates."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import compat


@pytest.mark.parametrize('offset',[0x138832,0x1100b80])
@pytest.mark.parametrize('lut',[False,True])
def test_published_v2_native_fields_upgrade_and_reapply_without_touching_other_bytes(offset,lut):
    layout,*_=compat.modules()
    native=layout.fl23_native_windows()
    path=Path(compat.core.BASE)/'assets/fl23-native-history.json'
    history=json.loads(path.read_text(encoding='utf-8'))
    previous=next(bytes.fromhex(w['after_hex']) for w in history['windows']
        if int(w['file_offset'],16)==offset and w.get('source_commit','').startswith('7d47a07'))
    if offset==0x138832:
        assert previous.hex()=='002c04d1132802d3142898bf002049f6f811c0f22011704700bf'
    current=next(w for w in json.loads((compat.core.BASE/'assets/patches.json').read_text())[1]['writes']
                 if int(w['file_offset'],16)==offset)
    cp=bytearray(offset+len(previous)+64);cp[32:40]=b'KEEPBODY'
    cp[offset:offset+len(previous)]=previous
    cp[0x138814:0x138818]=bytes.fromhex('3720e7e7')
    cp[0x13882e:0x138832]=bytes.fromhex('3820dae7')
    # For the small helper fixture the call-site positions already fit; for
    # the curve fixture they remain independent and must also be preserved.
    before=bytes(cp)
    fake=SimpleNamespace(runtime=lambda:dict(ap_templates=[''] if lut else ['nonmatching']),
        fl23_native_windows=lambda:native,fl23_previous_values=layout.fl23_previous_values)
    manifest=[dict(writes=[]),dict(writes=[current])]
    with patch.object(compat,'modules',return_value=(fake,None,None,None)), \
         patch.object(compat.core,'read_json',return_value=manifest):
        _,enabled,target,*_=compat.plan(b'AP',before,transitions=False)
        assert not enabled
        expected=bytearray(before);expected[offset:offset+len(previous)]=bytes.fromhex(current['after_hex'])
        assert target['cpapp-b.bin']==bytes(expected)
        assert target['appFw.so']==b'AP'
        assert compat.plan(b'AP',target['cpapp-b.bin'],transitions=False)[1]
        wrong=bytearray(before);wrong[offset+3]^=0x41
        with pytest.raises(ValueError,match='충돌'):
            compat.plan(b'AP',bytes(wrong),transitions=False)
