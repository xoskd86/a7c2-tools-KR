from pathlib import Path
import math
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import media
import native_codec


class AudioSelectionTests(unittest.TestCase):
    def test_wav_selects_only_requested_samples_without_ffmpeg_or_source_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source, selected = root / 'source.wav', root / 'selected.wav'
            samples = [i - 500 for i in range(1000)]
            native_codec.write_wav(source, samples, 1000)
            before = source.read_bytes()
            with patch.object(media, 'ffmpeg_path', side_effect=AssertionError('ffmpeg not needed')):
                media.extract(source, selected, start=.3, duration=.1)
            self.assertEqual(native_codec.read_wav(selected), (samples[300:400], 1000))
            self.assertEqual(source.read_bytes(), before)

    def test_quiet_audio_is_normalized_and_stays_within_peak_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'quiet.wav'
            native_codec.write_wav(source, [round(100 * math.sin(i * .1)) for i in range(2400)], 24000)
            report = media.convert(source, root / 'candidate', peak=.4)
            self.assertAlmostEqual(report['peak_fraction'], .4, places=4)
            payload = (root / 'candidate/custom-native.private.bin').read_bytes()
            self.assertLessEqual(max(map(abs, native_codec.decode(payload))), 13200)

    def test_invalid_selection_or_silent_input_does_not_produce_audio_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'silent.wav'
            native_codec.write_wav(source, [0] * 2400, 24000)
            for n, options in enumerate(({'start': -1}, {'duration': 3}, {'peak': float('nan')}, {})):
                output = root / str(n)
                with self.assertRaises(ValueError): media.convert(source, output, **options)
                self.assertFalse((output / 'custom-native.private.bin').exists())

    def test_silent_lead_in_names_the_selection_and_later_sound_converts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'lead.wav'
            # 10 ms of near-silence (dither), then a tone: a typical file opening.
            native_codec.write_wav(source, [(-1) ** i for i in range(240)] +
                                   [round(8000 * math.sin(i * .1)) for i in range(2400)], 24000)
            with self.assertRaisesRegex(ValueError, '소리가 거의 없습니다.*시작점'):
                media.convert(source, root / 'silent', start=0, duration=.01)
            self.assertFalse((root / 'silent').exists())
            media.convert(source, root / 'tone', start=.01, duration=.05)
            self.assertTrue((root / 'tone/custom-native.private.bin').is_file())


if __name__ == '__main__': unittest.main()
