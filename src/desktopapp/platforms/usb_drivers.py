"""Camera-only Windows driver setup and macOS libusb preparation.

No automatic driver changes on startup. Only Sony VID 054C, PIDs 0E8A/0336
are accepted. Existing usable WinUSB/libusbK/libusb0 bindings are retained.
"""
import ctypes
import codecs
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from ..paths import ROOT, data_root, write_json
from .macos_media import unmount_camera_volume

PIDS = {'0e8a': 'Sony A7C II - Mass Storage', '0336': 'Sony A7C II - Service'}
WDI_URL = 'https://github.com/Timocop/libwdi-wdi-releases/releases/download/v1.0/x64.zip'


def scan():
    report = {'platform': sys.platform, 'architecture': platform.machine(),
              'camera_io': False, 'devices': [], 'errors': []}
    if sys.platform == 'win32':
        try:
            from .windows_pnp import devices
            report['devices'] = devices()
        except Exception as exc:
            report['errors'].append({'stage': 'windows-pnp', 'error': repr(exc)})
    try:
        import usb.core
        import usb.util
        import usb.backend.libusb1
        import libusb_package
        backend = usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
        report['libusb_available'] = backend is not None
        if backend:
            report['libusb_devices'] = []
            for d in usb.core.find(find_all=True, idVendor=0x054c, backend=backend):
                row = {'vid': '054c', 'pid': f'{d.idProduct:04x}', 'known_camera_identity': f'{d.idProduct:04x}' in PIDS}
                try:
                    row['product'] = usb.util.get_string(d, d.iProduct) if d.iProduct else ''
                    row['interfaces'] = [{'class': i.bInterfaceClass, 'subclass': i.bInterfaceSubClass,
                        'protocol': i.bInterfaceProtocol, 'number': i.bInterfaceNumber} for config in d for i in config]
                except Exception as exc:
                    row['descriptor_error'] = repr(exc)
                finally:
                    usb.util.dispose_resources(d)
                report['libusb_devices'].append(row)
            if any(not row['known_camera_identity'] for row in report['libusb_devices']):
                report['usb_identity_note'] = '모든 Sony USB ID를 나열했습니다. 알 수 없는 PID가 확인된 A7C II라는 뜻은 아닙니다. 장치 이름, 인터페이스 종류 및 대용량 저장장치 모드를 확인하세요. 알 수 없는 장치의 드라이버는 자동으로 바꾸지 않습니다.'
    except Exception as exc:
        report['errors'].append({'stage': 'libusb-enumeration', 'error': repr(exc)})
    if sys.platform == 'darwin':
        report['driver_required'] = False
        report['note'] = 'macOS uses bundled libusb. Close Photos/Image Capture and unmount only camera storage volumes before service access.'
    return report


def installer():
    bundled = ROOT / 'desktop-assets/windows/wdi-simple.exe'
    if bundled.is_file():
        return bundled
    cached = data_root() / 'drivers/wdi-simple.exe'
    if not cached.exists():
        cached.parent.mkdir(parents=True, exist_ok=True)
        target = cached.parent / 'libwdi-x64.zip'
        urllib.request.urlretrieve(WDI_URL, target)
        with zipfile.ZipFile(target) as archive:
            entry = next(n for n in archive.namelist() if Path(n).name.lower() == 'wdi-simple.exe')
            cached.write_bytes(archive.read(entry))
    return cached


def driver_arguments(executable, pid, folder):
    pid = str(pid).lower()
    if pid not in PIDS:
        raise ValueError('Driver installation is limited to camera PIDs 0E8A and 0336')
    return [str(executable), '--vid', '0x054C', '--pid', '0x' + pid,
            '--type', '0', '--name', PIDS[pid], '--dest', str(folder), '--log', '0']


def elevate_request(arguments, job, action='driver_install'):
    """Elevate a fixed driver action, with a real process handle and full log."""
    from ..jobs import worker_command
    request = job / ('elevated-' + action + '-' + str(arguments.get('pid', 'camera'))) / 'request.private.json'
    elevated = request.parent
    elevated.mkdir(parents=True)
    write_json(request, {'schema': 'a7c2-desktop-job/1', 'job_dir': str(elevated),
                        'action': action, 'arguments': dict(arguments, elevated=True)})
    command = worker_command(request)
    from ctypes import wintypes
    class SHELLEXECUTEINFO(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('fMask', ctypes.c_ulong),
                    ('hwnd', wintypes.HWND), ('lpVerb', wintypes.LPCWSTR),
                    ('lpFile', wintypes.LPCWSTR), ('lpParameters', wintypes.LPCWSTR),
                    ('lpDirectory', wintypes.LPCWSTR), ('nShow', ctypes.c_int),
                    ('hInstApp', wintypes.HINSTANCE), ('lpIDList', ctypes.c_void_p),
                    ('lpClass', wintypes.LPCWSTR), ('hkeyClass', wintypes.HKEY),
                    ('dwHotKey', wintypes.DWORD), ('hIcon', wintypes.HANDLE),
                    ('hProcess', wintypes.HANDLE)]
    info = SHELLEXECUTEINFO()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40
    info.lpVerb = 'runas'
    info.lpFile = command[0]
    info.lpParameters = subprocess.list2cmdline(command[1:])
    info.nShow = 0
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFO)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    print('Windows 관리자 확인을 기다리는 중…', flush=True)
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    print('관리자 프로세스가 시작되었습니다. 진행 상황을 실시간으로 읽는 중입니다.', flush=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    tail = LiveLog(elevated / 'worker.log')
    try:
        while True:
            wait = kernel.WaitForSingleObject(info.hProcess, 500)
            tail.forward()
            if wait == 258:
                continue
            if wait != 0:
                raise ctypes.WinError(ctypes.get_last_error())
            break
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(info.hProcess)
    tail.forward(final=True)
    return elevated_result(elevated, code.value)


class LiveLog:
    """Forward new administrator log bytes without duplicating finished output."""
    def __init__(self, path):
        self.path, self.offset = Path(path), 0
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')

    def forward(self, *, final=False):
        raw = b''
        if self.path.exists():
            with self.path.open('rb') as stream:
                stream.seek(self.offset)
                raw = stream.read()
                self.offset = stream.tell()
        text = self.decoder.decode(raw, final=final)
        if text:
            print(text, end='', flush=True)


def elevated_result(folder, code):
    """Preserve the administrator worker's actual error instead of hiding it."""
    status = Path(folder) / 'status.private.json'
    if status.exists():
        value = json.loads(status.read_text(encoding='utf-8'))
        if value.get('phase') == 'SUCCEEDED' and code == 0:
            return value['result']
        if value.get('error'):
            raise RuntimeError(value['error'])
    raise RuntimeError('Administrator driver process did not complete: ' + str(code))


def install(arguments, job, *, report=None):
    if sys.platform == 'darwin':
        return scan()
    if sys.platform != 'win32':
        raise ValueError('Automatic driver installation is supported on Windows only')
    pid = str(arguments.get('pid', '0e8a')).lower()
    if pid not in PIDS:
        raise ValueError('Unsupported USB identity')
    print('Sony 054C:' + pid.upper() + ' 의 기존 드라이버를 검사하는 중…', flush=True)
    before = scan() if report is None else report
    write_json(job / 'driver-before.private.json', before)
    write_json(job / ('driver-before-' + pid + '.private.json'), before)
    matching = [d for d in before['devices'] if ('PID_' + pid.upper()) in d['id'].upper()]
    if not matching:
        query_errors=[e for e in before.get('errors',[]) if e.get('stage')=='windows-pnp']
        if query_errors:
            observed=', '.join('054C:'+row['pid'].upper()+' '+row.get('product','')
                for row in before.get('libusb_devices',[])) or '읽을 수 있는 장치 없음'
            raise RuntimeError('Windows 드라이버 조회에 실패했습니다. 이것만으로 카메라가 연결되지 않았다고 판단할 수 없습니다. 현재 libusb 검사: '+observed+
                '. 이번에는 드라이버를 설치하거나 카메라에 쓰지 않았습니다. 원래 조회 오류: '+json.dumps(query_errors,ensure_ascii=False))
        detected=[]
        for row in before['devices']:
            match=re.search(r'VID_054C&PID_[0-9A-F]{4}(?:&MI_[0-9A-F]{2})?',row['id'],re.I)
            detected.append((match.group(0) if match else 'Sony USB')+' '+str(row.get('description') or row.get('name') or ''))
        details='; '.join(detected) or 'Sony USB 장치를 찾지 못했습니다'
        raise RuntimeError(('카메라의 대용량 저장장치 ID 054C:0E8A를 찾지 못했습니다. 카메라에서 "대용량 저장장치"를 선택한 뒤 다시 연결하세요.' if pid=='0e8a' else
                            '서비스 ID 054C:0336을 찾지 못했습니다. 먼저 "2단계 드라이버 준비 및 연결 테스트"를 사용하세요.')+
                           '현재 검사: '+details+'. 전체 장치／인터페이스 정보를 저장했으며, 알 수 없는 PID의 드라이버는 자동으로 바꾸지 않습니다.')
    if all(str(d.get('service', '')).lower() in ('winusb', 'libusbk', 'libusb0') for d in matching):
        services = ', '.join(sorted({str(d['service']) for d in matching}))
        print('054C:%s에 %s이(가) 설치되어 있어 그대로 사용합니다. 드라이버 설치와 관리자 요청을 건너뜁니다.' % (pid.upper(), services), flush=True)
        result = {'driver_changed': False, 'pid': pid, 'note': '사용 가능한 드라이버가 설치되어 있어 그대로 사용하며, 다시 설치하지 않았습니다', 'report': before}
        write_json(job / ('driver-result-' + pid + '.private.json'), result)
        return result
    if not ctypes.windll.shell32.IsUserAnAdmin():
        if arguments.get('elevated'):
            raise PermissionError('Windows administrator permission was not granted')
        return elevate_request(arguments, job)
    exe = installer()
    command = driver_arguments(exe, pid, job / ('driver-' + pid))
    print('Installing WinUSB for Sony 054C:' + pid.upper(), flush=True)
    try:
        done = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                              errors='replace', cwd=job, creationflags=0x08000000,timeout=180)
    except subprocess.TimeoutExpired as exc:
        print(exc.stdout or '',flush=True);print(exc.stderr or '',file=sys.stderr,flush=True)
        raise RuntimeError('드라이버 설치가 180초를 넘어 대기를 중지했습니다. 로그를 보존하고 현재 바인딩을 확인하세요. 자동으로 다시 설치하지 않습니다.') from exc
    print(done.stdout, flush=True)
    print(done.stderr, file=sys.stderr, flush=True)
    after = scan()
    write_json(job / 'driver-report.private.json', after)
    if done.returncode:
        raise RuntimeError('libwdi installer exit code ' + str(done.returncode))
    result = {'driver_changed': True, 'pid': pid, 'report': after}
    write_json(job / ('driver-result-' + pid + '.private.json'), result)
    return result


def host_pids():
    """Present Sony PIDs from Windows SetupAPI, including driverless devices.

    Enumeration only. A failed query adds nothing, leaving libusb's view.
    """
    try:
        from .windows_pnp import devices
        rows = devices()
    except Exception:
        return []
    found = []
    for row in rows:
        match = re.match(r'^USB\\VID_054C&PID_([0-9A-F]{4})(?:[\\&]|$)',str(row.get('id', '')), re.I)
        if match and match[1].lower() not in found:
            found.append(match[1].lower())
    return found


def prepare(job):
    """Explicit two-stage setup; install only a currently present camera PID."""
    from looktool.devicesession import Camera, modules
    transport, _ = modules()
    results = []
    if sys.platform == 'win32':
        results.append(install({'pid': '0e8a'}, job))

        def service_driver():
            print('서비스 ID 054C:0336을 기다리는 중입니다. 나타나면 먼저 드라이버를 검사하고, 사용 가능한 바인딩이 있으면 설치를 건너뜁니다.', flush=True)
            deadline = time.monotonic() + 20
            while True:
                report = scan()
                if any('PID_0336' in d['id'].upper() for d in report['devices']):
                    break
                if time.monotonic() >= deadline:
                    write_json(job / 'service-driver-enumeration.private.json', report)
                    raise RuntimeError('Sony service PID 0336 did not appear. Preserve this log and reconnect before another setup attempt.')
                time.sleep(.5)
            print('서비스 ID가 나타났습니다. 기존 드라이버를 검사합니다.', flush=True)
            results.append(install({'pid': '0336'}, job, report=report))

        transport.SERVICE_DRIVER_SETUP = service_driver
        transport.HOST_SONY_PIDS = host_pids
    try:
        print('카메라 서비스 연결을 설정하는 중입니다(이 단계에서는 펌웨어에 쓰지 않음). 두 USB ID를 각각 검사합니다.', flush=True)
        with Camera() as camera:
            print('서비스 연결에 성공했습니다. 드라이버 준비가 끝났습니다.', flush=True)
            return {'model': camera.identity['model'], 'service_connected': True,
                    'camera_firmware_written': False, 'driver_results': results}
    finally:
        transport.SERVICE_DRIVER_SETUP = None
        transport.HOST_SONY_PIDS = None


def driver_inventory():
    """All signed PnP bindings, including other devices sharing an OEM INF."""
    print('Windows 드라이버 바인딩을 읽는 중…', flush=True)
    script = r"$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new(); Get-CimInstance Win32_PnPSignedDriver | ForEach-Object {[pscustomobject]@{id=$_.DeviceID;name=$_.DeviceName;inf=$_.InfName;provider=$_.DriverProviderName}} | ConvertTo-Json -Compress"
    done = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                          capture_output=True, text=True, encoding='utf-8', errors='replace',
                          timeout=60, creationflags=0x08000000)
    if done.returncode:
        print(done.stdout, flush=True)
        print(done.stderr, file=sys.stderr, flush=True)
        raise RuntimeError('Windows driver inventory failed, exit code ' + str(done.returncode))
    if done.stderr:
        print(done.stderr, file=sys.stderr, flush=True)
    try:
        rows = json.loads(done.stdout) if done.stdout.strip() else []
    except ValueError:
        print('Raw Windows driver inventory:', done.stdout, flush=True)
        raise
    return rows if isinstance(rows, list) else [rows]


def is_camera_id(value, pids):
    match = re.match(r'^USB\\VID_054C&PID_([0-9A-F]{4})(?:[\\&]|$)', str(value), re.I)
    return bool(match and match[1].lower() in pids)


def inf_hardware_ids(text):
    r"""Resolve libusbK/libwdi INF [Strings] references before reading USB IDs.

    Zadig libusbK uses USB\%DeviceID%; WinUSB commonly uses a literal ID.
    Both describe the same device. Comments and unused string values do not
    constitute model bindings.
    """
    section, strings, content = '', {}, []
    for line in text.lstrip('\ufeff').splitlines():
        # INF comments start outside quoted strings; quoted semicolons belong
        # to string values, not comments.
        line = re.split(r';(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)', line, maxsplit=1)[0].strip()
        if not line:
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1].strip().casefold()
        elif section == 'strings' and '=' in line:
            key, value = line.split('=', 1)
            strings[key.strip().casefold()] = value.strip().strip('"').replace('""', '"')
        else:
            content.append(line)

    def expand(value, seen=frozenset()):
        def replace(match):
            key = match[1].casefold()
            if key not in strings or key in seen:
                return match[0]
            return expand(strings[key], seen | {key})
        return re.sub(r'%([^%]+)%', replace, value)

    expanded = expand('\n'.join(content))
    return re.findall(r'USB\\VID_([0-9a-f]{4})&PID_([0-9a-f]{4})(?=[\\&,\s\"]|$)', expanded, re.I)


def uninstall_plan(rows, pids, inf_folder):
    """Never remove a native INF or a package used by another type of device."""
    selected = [row for row in rows if is_camera_id(row.get('id'), pids)]
    names = sorted({str(row.get('inf') or '').lower() for row in selected
                    if re.fullmatch(r'oem\d+\.inf', str(row.get('inf') or ''), re.I)})
    for name in names:
        users = [row for row in rows if str(row.get('inf') or '').lower() == name]
        if any(not is_camera_id(row.get('id'), pids) for row in users):
            raise ValueError('Driver package is shared with a non-selected device; not removed: ' + name)
        raw = (Path(inf_folder) / name).read_bytes()
        text = raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8', errors='replace')
        ids = inf_hardware_ids(text)
        if not ids:
            raise ValueError('Could not resolve USB hardware IDs in driver INF; not removed: ' + name)
        if any(vid.lower() != '054c' or pid.lower() not in pids for vid, pid in ids):
            raise ValueError('Driver INF targets another device; not removed: ' + name)
        print('Camera driver package:', name, 'resolved USB IDs:',
              ', '.join(sorted({vid.upper() + ':' + pid.upper() for vid, pid in ids})), flush=True)
    return names


def decode_windows_output(raw):
    if not raw:
        return ''
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16', errors='replace')
    if b'\0' in raw[:64]:
        return raw.decode('utf-16-le', errors='replace')
    encodings = ['utf-8-sig', 'mbcs', 'oem']
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except (UnicodeError, LookupError):
            continue
    return raw.decode('utf-8', errors='replace')


def run_pnputil(arguments, *, log_dir=None):
    exe = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'System32/pnputil.exe'
    command = [str(exe), *map(str, arguments)]
    print('Windows driver command:', subprocess.list2cmdline(command), flush=True)
    try:
        done = subprocess.run(command, capture_output=True, timeout=180, creationflags=0x08000000)
    except subprocess.TimeoutExpired as exc:
        print(decode_windows_output(exc.stdout or b''), flush=True)
        print(decode_windows_output(exc.stderr or b''), file=sys.stderr, flush=True)
        raise RuntimeError('Windows driver command timed out after 180 seconds. Inspect driver status before retrying.') from exc
    if log_dir:
        label = str(arguments[0]).lstrip('/') + '-' + str(time.time_ns())
        for name, raw in [('stdout', done.stdout), ('stderr', done.stderr)]:
            (Path(log_dir) / (label + '-' + name + '.raw')).write_bytes(raw)
    print(decode_windows_output(done.stdout), flush=True)
    print(decode_windows_output(done.stderr), file=sys.stderr, flush=True)
    print('PnPUtil exit code:', done.returncode, flush=True)
    if done.returncode not in (0, 3010):
        raise RuntimeError('Windows driver operation stopped, exit code ' + str(done.returncode))
    return done.returncode


def uninstall(arguments, job):
    """User-requested Windows driver removal. Never opens a camera session."""
    if sys.platform != 'win32':
        raise ValueError('macOS uses libusb and has no camera driver to uninstall')
    pids = {str(pid).lower() for pid in arguments.get('pids', PIDS)}
    if not pids or not pids <= set(PIDS):
        raise ValueError('Driver removal is limited to Sony 054C:0E8A and 054C:0336')
    if not ctypes.windll.shell32.IsUserAnAdmin():
        if arguments.get('elevated'):
            raise PermissionError('Windows administrator permission was not granted')
        return elevate_request(arguments, job, action='driver_uninstall')
    before = driver_inventory()
    write_json(job / 'driver-uninstall-before.private.json', before)
    plans = uninstall_plan(before, pids, Path(os.environ.get('WINDIR', 'C:/Windows')) / 'INF')
    removed, reboot = [], False
    for name in plans:
        # Preserve the actual driver package before removing it. No forced
        # package deletion, global driver cleanup or automatic reboot.
        exported = job / 'exported-drivers' / name.removesuffix('.inf')
        exported.mkdir(parents=True)
        print('드라이버 패키지 백업 중: ' + name, flush=True)
        run_pnputil(['/export-driver', name, exported], log_dir=job)
        print('카메라 드라이버 패키지 제거 중: ' + name, flush=True)
        code = run_pnputil(['/delete-driver', name, '/uninstall'], log_dir=job)
        removed.append(name)
        reboot |= code == 3010
        write_json(job / 'driver-uninstall-progress.private.json',
                   {'removed': removed, 'reboot_required': reboot})
    if removed:
        print('Windows가 장치를 다시 인식하도록 하는 중…', flush=True)
        reboot |= run_pnputil(['/scan-devices'], log_dir=job) == 3010
    after = driver_inventory()
    write_json(job / 'driver-report.private.json', {'before': before, 'after': after,
               'removed': removed, 'reboot_required': reboot})
    return {'removed_packages': removed, 'reboot_required': reboot,
            'camera_firmware_written': False, 'driver_backup': str(job / 'exported-drivers'),
            'next_step': ('Windows requests a system restart to finish removal; no restart was initiated.' if reboot else
                         'Unplug/reconnect USB in Mass Storage mode so Windows can bind its default storage driver.')
                         if removed else 'No third-party camera driver package found; Windows native bindings were retained.'}
