"""Sound actions report the backend's reason and check conversion before USB."""
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'src/shutter_sound')]
from desktopapp.actions import sound
import native_codec


class SoundActionErrorTests(unittest.TestCase):
    def test_failed_conversion_raises_the_backend_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'silent.wav'
            native_codec.write_wav(source, [0] * 2400, 24000)
            with self.assertRaisesRegex(RuntimeError, '소리가 거의 없습니다') as caught:
                sound.run('sound_import', dict(source=str(source), start=0, duration=.01,
                                               workspace=str(root / 'sound')))
            self.assertIn('이번에는 카메라 읽기/쓰기를 하지 않았습니다', str(caught.exception))
            self.assertNotIn('see the complete backend log', str(caught.exception))

    def test_audition_precheck_needs_a_converted_clip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); work = root / 'sound'
            self.assertFalse(sound.has_conversion(work))
            source = root / 'tone.wav'
            native_codec.write_wav(source, [round(8000 * math.sin(i * .1)) for i in range(2400)], 24000)
            sound.run('sound_import', dict(source=str(source), start=0, duration=.05, workspace=str(work)))
            self.assertTrue(sound.has_conversion(work))
            # A selection whose payload is gone is not a usable conversion.
            selected = json.loads((work / 'conversion.private.json').read_text(encoding='utf-8'))['conversion']
            (Path(selected) / 'custom-native.private.bin').unlink()
            self.assertFalse(sound.has_conversion(work))


if __name__ == '__main__':
    unittest.main()
