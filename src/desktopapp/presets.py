"""Bundled six-look selection, resolved before opening a device.

The actual A7Look/DCP assets are distribution attachments, not MIT source.
No fitting or downloading occurs during installation.
"""
import copy
from pathlib import Path
from .paths import ROOT

PACK = 'default-six-v1'
NAMES = ('Classic Neg.', 'Classic Chrome', 'Nostalgic Neg.', 'Astia',
         'GR Positive Film', 'GR Negative Film')
SUMMARY = '1 NC · 2 CC · 3 NN · 4 Astia · 5 GR Positive · 6 GR Negative'


def catalog():
    """Default file choices without disk access or validation during UI startup."""
    return [dict(slot=slot, path=str(ROOT / 'desktop-assets/presets' / PACK / ('slot%d.a7look' % slot)), name=name)
            for slot, name in enumerate(NAMES, 1)]


def resolve(options):
    """Expand an explicit pack choice; ordinary selected-slot jobs stay intact."""
    options = copy.deepcopy(options)
    pack = options.get('preset_pack')
    if not pack:
        return options
    if pack != PACK:
        raise ValueError('Unknown built-in look pack: ' + str(pack))
    if options.get('looks'):
        raise ValueError('Choose the built-in six looks or custom files, not both')
    from looktool import lookfile
    rows = []
    for row in catalog():
        slot, name, path = row['slot'], row['name'], Path(row['path'])
        if not path.is_file():
            raise ValueError('내장 룩 6종 파일이 없습니다. 전체 도구 패키지를 사용하세요. 아직 카메라에 연결하지 않았습니다: ' + str(path))
        doc = lookfile.read(path)
        lookfile.writer_document(doc)
        if doc['name'] != name or not doc.get('native_photo'):
            raise ValueError('내장 룩 이름／사진 단계 불일치: ' + str(path))
        rows.append(dict(slot=slot, path=str(path), name=name))
    options['looks'] = rows
    return options
