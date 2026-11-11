"""Run the real ARM64 backup-cleanup worker against a simulated /usr/share/app.

Synthetic syscalls test which names are removed, the mount sequence and the
report. This does not establish camera behavior.
"""
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unicorn import UC_HOOK_INTR
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
    UC_ARM64_REG_X3, UC_ARM64_REG_X8)
from looktool import checkpointworker, nameworker

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('purge_elf_loader', ROOT / 'dev/shutter_sound/verify_worker.py')
sys.path.insert(0, str(ROOT / 'dev/shutter_sound'))
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)

DIR = '/usr/share/app/'
REQUEST = '/tmp/jiritsu/checkpoint.names.request'
RESULT = '/tmp/jiritsu/checkpoint.names.result'
PLUGIN = '/tmp/jiritsu/libJit.so'
OWN = 'feedfacefeedface'


class FS:
    def __init__(self, entries, unlink_fault=None):
        # name -> 'file' | 'dir'
        self.entries = dict(entries)
        self.ro = True
        self.unlink_fault = unlink_fault
        self.unlinked = []
        self.files = {}

    def run(self, worker, mode=5):
        cpu, start, end = loader.load(worker)
        self.files[REQUEST] = struct.pack('<8sII', b'A7NAM001', mode, 0)
        self.files[PLUGIN] = b'owned'
        handles, counter = {}, [10]

        def string(at):
            value = bytearray()
            while len(value) < 1024:
                c = bytes(cpu.mem_read(at + len(value), 1))
                if c == b'\0':
                    return value.decode('ascii')
                value += c
            raise AssertionError('Unterminated syscall path')

        def dirents():
            out = bytearray()
            for ino, (name, kind) in enumerate(sorted(self.entries.items()), 100):
                raw = name.encode() + b'\0'
                size = (19 + len(raw) + 7) // 8 * 8
                out += struct.pack('<QqHB', ino, ino, size, 4 if kind == 'dir' else 8) + raw + bytes(size - 19 - len(raw))
            return bytes(out)

        def syscall(uc, number, user):
            no = uc.reg_read(UC_ARM64_REG_X8)
            a = [uc.reg_read(r) for r in (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3)]
            result = 0
            if no in (172, 175):
                result = 519 if no == 172 else 0
            elif no == 56:
                path, flags = string(a[1]), a[2]
                if path == '/proc/mounts':
                    data = ('/dev/nflasha15 /usr ext2 ' + ('ro' if self.ro else 'rw') + ',sync 0 0\n').encode()
                elif path == DIR.rstrip('/'):
                    data = dirents()
                elif flags & 64:
                    assert path == RESULT
                    self.files[path] = b''
                    data = None
                else:
                    data = self.files.get(path)
                    if data is None:
                        result = -2
                if not result:
                    counter[0] += 1
                    result = counter[0]
                    handles[result] = [path, data, 0]
            elif no == 57:
                handles.pop(a[0])
            elif no == 61:
                path, data, pos = handles[a[0]]
                assert path == DIR.rstrip('/')
                chunk = b''
                while pos < len(data):
                    size = struct.unpack_from('<H', data, pos + 16)[0]
                    if len(chunk) + size > a[2]:
                        break
                    chunk += data[pos:pos + size]
                    pos += size
                handles[a[0]][2] = pos
                if chunk:
                    uc.mem_write(a[1], chunk)
                result = len(chunk)
            elif no in (63, 67):
                path, data, pos = handles[a[0]]
                at = a[3] if no == 67 else pos
                chunk = data[at:at + a[2]]
                if chunk:
                    uc.mem_write(a[1], chunk)
                result = len(chunk)
                handles[a[0]][2] = at + result
            elif no == 64:
                path = handles[a[0]][0]
                assert path == RESULT
                self.files[RESULT] += bytes(uc.mem_read(a[1], a[2]))
                result = a[2]
            elif no == 79:
                path = string(a[1])
                assert path.startswith(DIR) and a[3] == 0x100
                kind = self.entries.get(path[len(DIR):])
                if kind is None:
                    result = -2
                else:
                    st = bytearray(128)
                    struct.pack_into('<QQII', st, 0, 15, 1, 0o040755 if kind == 'dir' else 0o100644, 1)
                    uc.mem_write(a[2], bytes(st))
            elif no == 35:
                path = string(a[1])
                if path == PLUGIN:
                    self.files.pop(PLUGIN)
                else:
                    assert not self.ro and path.startswith(DIR)
                    name = path[len(DIR):]
                    if self.unlink_fault and name == self.unlink_fault:
                        result = -5
                    else:
                        assert self.entries.pop(name) == 'file'
                        self.unlinked.append(name)
            elif no == 40:
                assert string(a[1]) == '/usr'
                self.ro = bool(a[3] & 1)
            elif no == 82:
                assert handles[a[0]][0] in (DIR.rstrip('/'), RESULT)
            else:
                raise AssertionError('Unexpected syscall ' + str(no))
            uc.reg_write(UC_ARM64_REG_X0, result & ((1 << 64) - 1))
        cpu.hook_add(UC_HOOK_INTR, syscall)
        cpu.emu_start(start, end, count=100_000_000)
        raw = bytes(self.files[RESULT])
        assert len(raw) == nameworker.REPORT.size
        return raw, nameworker.REPORT.unpack(raw)


class PurgeNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.build = checkpointworker.build_purge(Path(cls.temp.name) / 'purge', OWN)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def layout(self):
        files = nameworker.filenames()
        doomed = ['.a7c2-lut-3e57ffe746c143e3-' + files[0] + '.original',
                  '.a7c2-lut-b8e90caf34ac44ac-' + files[-1] + '.original',
                  '.a7c2-checkpoint-0123456789abcdef-' + files[5] + '.original',
                  '.a7c2-lut-0947d8b5fc1d41ee-' + files[7] + '.stage']
        kept = [*files[:10],
                '.a7c2-checkpoint-' + OWN + '-' + files[1] + '.original',   # this restore's own backup
                '.a7c2-repair-4b82e6d1-' + files[2] + '.original',          # not a project job name
                '.a7c2-lut-3E57FFE746C143E3-' + files[3] + '.original',     # not lowercase hex
                '.a7c2-lut-3e57ffe746c143e-' + files[3] + '.original',      # 15 hex digits
                '.a7c2-lut-3e57ffe746c143e3-unknown_resource.uxc.original',
                '.a7c2-lut-3e57ffe746c143e3-' + files[4] + '.original.bak',
                'a7c2displace-00000000000000000000000000000000.tmp']
        entries = {name: 'file' for name in doomed + kept}
        entries['.a7c2-lut-1111111111111111-' + files[6] + '.original'] = 'dir'
        return entries, doomed, kept

    def test_removes_only_project_backup_names_and_remounts_readonly(self):
        entries, doomed, kept = self.layout()
        fs = FS(entries)
        raw, v = fs.run(self.build['path'])
        self.assertEqual(v[3:6], (9, 68, 0))
        self.assertEqual((v[12], v[16]), (5, 4))  # the matching directory is skipped, not removed
        self.assertEqual(sorted(fs.unlinked), sorted(doomed))
        self.assertTrue(all(name in fs.entries for name in kept))
        self.assertTrue(fs.ro)
        self.assertNotIn(PLUGIN, fs.files)
        self.assertEqual(checkpointworker.purge_report(raw, self.build), {'mode': 5, 'matched': 5, 'removed': 4})

    def test_nothing_to_remove_never_remounts(self):
        files = nameworker.filenames()
        fs = FS({name: 'file' for name in files})
        fs.ro = True
        raw, v = fs.run(self.build['path'])
        self.assertEqual(v[3:6], (9, 68, 0))
        self.assertEqual((v[7], v[12], v[16]), (0, 0, 0))
        self.assertEqual(fs.unlinked, [])

    def test_unlink_error_stops_and_still_returns_readonly(self):
        entries, doomed, _ = self.layout()
        fs = FS(entries, unlink_fault=sorted(doomed)[1])
        raw, v = fs.run(self.build['path'])
        self.assertEqual(v[5], -5)
        self.assertTrue(fs.ro)
        with self.assertRaisesRegex(ValueError, '정리가 완료되지 않았'):
            checkpointworker.purge_report(raw, self.build)

    def test_restore_build_rejects_cleanup_mode(self):
        from unittest.mock import patch
        from looktool import resources
        files = nameworker.filenames()[:2]
        raw = bytearray(b'uxc\x07\0\0\0\0' + struct.pack('<HHI', 8, 5, 2860 | (2860 << 12)) + b'\xff\xff' * 2860)
        struct.pack_into('<H', raw, 16, 0)
        raw.extend(struct.pack('<I', 0x1e00a000) + b'Native\0\0')
        original = bytes(raw); named = resources.rename(original, {1: 'Classic Neg.'})[0]
        with patch.object(nameworker, 'filenames', return_value=files):
            build = checkpointworker.build_names({'resources/' + n: named for n in files},
                {'resources/' + n: original for n in files}, Path(self.temp.name) / 'restore', 'restore')
        entries, _, _ = self.layout()
        fs = FS(entries)
        raw, v = fs.run(build['path'], 5)
        self.assertEqual(v[5], -22)
        self.assertEqual(fs.unlinked, [])

    def test_purge_build_rejects_every_other_mode(self):
        entries, _, _ = self.layout()
        for mode in (0, 1, 2, 3, 4):
            with self.subTest(mode=mode):
                fs = FS(entries)
                raw, v = fs.run(self.build['path'], mode)
                self.assertEqual(v[5], -22)
                self.assertEqual(fs.unlinked, [])


if __name__ == '__main__':
    unittest.main()
