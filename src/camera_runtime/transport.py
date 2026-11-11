"""Bounded Sony service transport. All device access stays in this module."""
import struct
import sys
import time
import core
import usb.core
import usb.util
import usb.backend.libusb1
import libusb_package
from pmca.usb.driver.generic import GenericUsbDriver,MscBbbDriver,GenericUsbException
from pmca.usb.driver.generic.libusb import UsbBackend
from io import BytesIO
from pmca.usb.sony import SonySenserAuthDevice,SonySenserCamera,SonyExtCmdCamera,SonyMscExtCmdDevice
from pmca.usb.sony import SonySenserDevice as PmcaSenserDevice
from pmca.usb import MscDevice

class SenserProtocolError(RuntimeError):
    """The reply cannot be associated with the current request."""


class SonySenserDevice(PmcaSenserDevice):
    """Use length-delimited reads, finishing direct CP unit reads explicitly.

    File/settings snapshots retain the field-tested v2 no-eager-read path.
    Direct CP reads finish their aligned response before the immediate WRITE41.
    Preserve any nonempty lookahead rather than discarding real data.
    """
    RESPONSE_FRAMING='length-delimited-zlp/3'
    END_PACKET_MS=1000

    def __init__(self,driver):
        super().__init__(driver)
        self._failed=False
        self._pending=bytearray()

    def _end_packet(self):
        if self._pending:return
        try:data=self.driver.read(self.SenserMinSize,timeout=self.END_PACKET_MS)
        except usb.core.USBTimeoutError:return
        except GenericUsbException as exc:
            if not isinstance(exc.__cause__ or exc.__context__,usb.core.USBTimeoutError):raise
            return
        # Some firmware omits the empty transfer. The read may have returned
        # real continuation data instead; it belongs to the next normal read.
        if data:self._pending.extend(data)

    def _read_nonempty(self,length):
        if self._pending:
            data=bytes(self._pending[:length]);del self._pending[:length]
            return data
        while True:
            data=self.driver.read(length)
            if data:return data

    def _readAll(self,length):
        chunks=[];done=0
        while done<length:
            data=self._read_nonempty(length-done)
            if len(data)>length-done:
                raise SenserProtocolError('Senser payload exceeds requested length')
            chunks.append(data);done+=len(data)
        return b''.join(chunks)

    def sendSenserPacket(self,pFunc,data,oData=None):
        if self._failed:raise SenserProtocolError('Senser channel is unusable after a failed exchange; no request sent')
        try:return self._exchange(pFunc,data,oData)
        except BaseException:
            self._failed=True
            raise

    def _exchange(self,pFunc,data,oData):
        finish_cp_read=(pFunc==SonySenserCamera.SONY_PFUNC_AdjustControl
                        and data[:4]==struct.pack('<HH',0x603,0x40))
        while data!=b'':
            header=self.SenserPacketHeader.pack(size=len(data),pFunc=pFunc,sequence=self._sequence,
                                                version=0,miconType=0,offsetType=0,response=0)
            self.driver.write(header+data[:self.SenserMaxSize])
            data=data[self.SenserMaxSize:]
        outData=BytesIO() if oData is None else oData
        minSize=0 if self.driver.getId()[1]==0x0336 else self.SenserMinSize
        while True:
            l=max(minSize-self.SenserPacketHeader.size,0)
            d=self._read_nonempty(self.SenserPacketHeader.size+l)
            if len(d)<self.SenserPacketHeader.size:
                raise SenserProtocolError('Short senser header: expected_bytes=%d actual_bytes=%d expected_sequence=%d'%(
                    self.SenserPacketHeader.size,len(d),self._sequence))
            header=self.SenserPacketHeader.unpack(d)
            if header.sequence!=self._sequence or header.pFunc!=pFunc:
                raise SenserProtocolError('Wrong senser sequence or function: expected_sequence=%d actual_sequence=%d '
                    'expected_pfunc=%#x actual_pfunc=%#x declared_bytes=%d'%(
                    self._sequence,header.sequence,pFunc,header.pFunc,header.size))
            outData.write(d[self.SenserPacketHeader.size:])
            done=l
            dataLen=min(header.size,self.SenserMaxSize)
            chunkLen=self.SenserChunkSize-minSize
            while done<dataLen:
                l=min(dataLen-done,chunkLen)
                outData.write(self._readAll(l))
                done+=l
                if finish_cp_read and not (l&(self.SenserMinSize-1)):self._end_packet()
                chunkLen=self.SenserChunkSize
            if header.size<=self.SenserMaxSize:break
        self._sequence+=1
        return header.response,outData.getvalue() if oData is None else None


class Bounded(UsbBackend):
    def __init__(self,device,budget=180):
        super().__init__(device);self.budget=budget;self.reset()
    def reset(self):self.deadline=time.monotonic()+self.budget
    def timeout(self,t=None):
        left=int((self.deadline-time.monotonic())*1000)
        if left<=0:raise TimeoutError('카메라 작업이 제한된 대기 시간을 초과했습니다')
        return max(1,min(left,t or self.budget*1000))
    def read(self,ep,length,timeout=None):return super().read(ep,length,self.timeout(timeout))
    def write(self,ep,data):
        self.dev.default_timeout=self.timeout()
        try:written=self.dev.write(ep,data)
        except usb.core.USBError as exc:raise GenericUsbException('USB command write failed') from exc
        if written!=len(data):
            raise GenericUsbException('Short USB command write: expected_bytes=%d actual_bytes=%s'%(len(data),written))

def get_device(pid):
    backend=usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
    if backend is None:raise RuntimeError('libusb 라이브러리를 찾을 수 없습니다')
    found=list(usb.core.find(find_all=True,idVendor=0x054c,idProduct=pid,backend=backend))
    if len(found)!=1:raise RuntimeError(f'대상 카메라를 정확히 한 대만 연결해야 합니다(USB {pid:04x}). 대용량 저장장치 모드와 드라이버를 확인하세요')
    if not list(found[0]):raise RuntimeError('USB 구성을 읽을 수 없습니다')
    return found[0]

# Initial USB startup fix v1: only the pre-authentication information query.
class DiagnosticUsbFailure(RuntimeError): pass
class ReadinessFailure(DiagnosticUsbFailure): pass
class SonyBusyFailure(DiagnosticUsbFailure): pass
# Optional desktop-only driver preparation between the two enumerations.
# CLI sessions leave it unset; it never retries authentication or firmware I/O.
SERVICE_DRIVER_SETUP = None
# Optional host enumeration of present Sony PIDs, set beside SERVICE_DRIVER_SETUP
# by the Windows desktop driver setup. Windows libusb does not list a device
# that has no driver yet, so a first-time 0336 is invisible to libusb alone.
# Enumeration only; it never opens the device.
HOST_SONY_PIDS = None
READY_WAIT=30
SONY_BUSY_WAIT=10
POLL_INTERVAL=1

class _InitialProgress:
    def event(self,kind,**values):
        if kind=='readiness' and values['attempt']==1 and values['sense']!=[0,0,0]:
            print('카메라가 USB 초기화를 마칠 때까지 기다리는 중입니다.',flush=True)
        elif kind=='sony_query_status' and values['attempt']==1 and values['sense']==[9,129,129]:
            print('카메라가 본체 정보를 준비하는 중입니다. 잠시 후 다시 읽습니다.',flush=True)

class _InitialBackend(Bounded):
    def __init__(self,device,report):
        UsbBackend.__init__(self,device)
        self.budget=60
        self.deadline=time.monotonic()+self.budget
    def reset(self):
        return UsbBackend.reset(self)
    def read(self,ep,length,timeout=None):
        try:
            return bytes(self.dev.read(ep,length,timeout=self.timeout(timeout or 5000)))
        except usb.core.USBError as exc:
            if getattr(exc,'backend_error_code',None)==-9:
                raise GenericUsbException('Initial USB endpoint stalled') from exc
            raise
    def write(self,ep,data):
        try:
            written=self.dev.write(ep,data,timeout=self.timeout(5000))
        except usb.core.USBError as exc:
            if getattr(exc,'backend_error_code',None)==-9:
                raise GenericUsbException('Initial USB endpoint stalled') from exc
            raise
        if written!=len(data):
            raise GenericUsbException('Short initial USB command write: expected_bytes=%d actual_bytes=%s'%(len(data),written))

def _query_initial_ready(device):
    report=_InitialProgress()
    backend_type=_InitialBackend
    from pmca.usb import MscDevice
    from pmca.usb.driver.generic import MscBbbDriver
    from pmca.usb.sony import SonyExtCmdCamera, SonyMscExtCmdDevice

    class ReadyDriver(MscBbbDriver):
        def sendCommand(self, command, failOnError=False):
            if command != bytes(6):
                raise DiagnosticUsbFailure('Only TEST UNIT READY is permitted here.')
            deadline = min(self.backend.deadline, time.monotonic() + READY_WAIT)
            attempt = 0
            while True:
                if time.monotonic() >= deadline:
                    raise ReadinessFailure('Camera did not become ready; Sony query was not started.')
                attempt += 1
                sense = super().sendCommand(command, failOnError)
                report.event('readiness', attempt=attempt, sense=list(sense))
                if sense == (0, 0, 0):
                    return sense
                # Retry only explicit becoming-ready and standard attention
                # states. Missing media and unknown responses stop immediately.
                if sense not in {(2, 4, 1), (6, 0x29, 0), (6, 0x28, 0)}:
                    raise ReadinessFailure('Camera is not ready; sense=%02x/%02x/%02x. Stopped.' % sense)
                time.sleep(min(POLL_INTERVAL, max(0, deadline - time.monotonic())))

    class BoundedInfoDevice(SonyMscExtCmdDevice):
        def sendSonyExtCommand(self, cmd, data, bufferSize):
            command = bytes([self.MSC_OC_ExtCmd]) + struct.pack('<I', cmd) + bytes(7)

            def exchange(direction):
                deadline = min(self.driver.backend.deadline, time.monotonic() + SONY_BUSY_WAIT)
                attempt = 0
                while True:
                    if time.monotonic() >= deadline:
                        raise SonyBusyFailure('Camera remained busy after readiness passed; query stopped.')
                    attempt += 1
                    if direction == 'out':
                        sense = self.driver.sendWriteCommand(command, data)
                        result = None
                    else:
                        sense, result = self.driver.sendReadCommand(command, bufferSize)
                    report.event('sony_query_status', direction=direction, attempt=attempt, sense=list(sense))
                    if sense != self.MSC_SENSE_DeviceBusy:
                        self._checkResponse(sense)
                        return result
                    # Retry only Sony's explicit busy response. A transfer
                    # timeout or disconnect propagates without another command.
                    time.sleep(min(POLL_INTERVAL, max(0, deadline - time.monotonic())))

            exchange('out')
            return exchange('in') if bufferSize else b''

    driver = ReadyDriver(backend_type(device, report))
    report.event('stage', name='scsi_inquiry')
    info = MscDevice(driver).getDeviceInfo()
    report.event('inquiry_ok', manufacturer=info.manufacturer.strip(), product=info.model.strip())
    if info.manufacturer.strip() != 'Sony':
        raise RuntimeError('Unexpected SCSI manufacturer; stopped before Sony extension.')
    report.event('stage', name='sony_get_camera_info')
    info = SonyExtCmdCamera(BoundedInfoDevice(driver)).getCameraInfo()
    # Do not persist info.serial, modelCode, plist, or raw transfer payloads.
    report.event('camera_info_ok', model=info.modelName)
    if info.modelName != 'ILCE-7CM2':
        raise RuntimeError('Unexpected model; this diagnostic is for ILCE-7CM2 only.')
    return info

def identity():
    dev=get_device(0x0e8a)
    try:
        info=_query_initial_ready(dev)
        if info.modelName!=core.MODEL:raise RuntimeError('지원 대상: '+core.MODEL)
        return {'model':info.modelName,'serial_sha256':core.sha(info.serial.encode())}
    finally:usb.util.dispose_resources(dev)

def service_identity(snapshot,model):
    """Keep the original normal-query binding without that application query."""
    if model!=core.MODEL:raise RuntimeError('지원 대상: '+core.MODEL)
    serial=core.backup_records(snapshot)[0].get(0x00e70003)
    if serial is None or len(serial.data)!=4:
        raise RuntimeError('서비스 백업에 4바이트 시리얼 번호 속성 00e70003이 없습니다')
    # getCameraInfo hexlifies its four serial bytes before the old tool hashes
    # the text. Hashing the raw bytes would break existing per-body backups.
    return {'model':model,'serial_sha256':core.sha(serial.data.hex().encode('ascii'))}

# Shortly after leaving service mode the camera may answer a new normal-side
# authentication by re-enumerating as 054C:0D68, then 096F, then MSC again,
# never as 0336 (field logs 28 and 30, 12-15 s after the previous exit).
SERVICE_COOLDOWN=30
SERVICE_ATTEMPTS=3
SERVICE_WAIT=20
_service_exit=None  # monotonic time the last service session ended in this process


def _cool_down():
    if _service_exit is None:return
    left=SERVICE_COOLDOWN-(time.monotonic()-_service_exit)
    if left>0:
        print('카메라가 이전 서비스 모드에서 복구되기를 기다리는 중(%d초).'%round(left),flush=True)
        time.sleep(left)


def _sony_pids():
    backend=usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
    found=['%04x'%d.idProduct for d in usb.core.find(find_all=True,idVendor=0x054c,backend=backend)]
    if HOST_SONY_PIDS is not None:
        for pid in HOST_SONY_PIDS():
            if pid not in found:found.append(pid)
    return found


def _wait_for(pid,seconds,seen,prepare=None):
    """Enumerate only; record every Sony identity seen while waiting."""
    deadline=time.monotonic()+seconds
    prepared=False
    while True:
        for found in _sony_pids():
            if found not in seen:seen.append(found)
        if prepare is not None and not prepared and '%04x'%pid in seen:
            prepare();prepared=True
            # Installing a driver can outlast the wait and re-enumerates the
            # device; give the rebound interface a fresh window to open.
            deadline=max(deadline,time.monotonic()+seconds)
        try:return get_device(pid)
        except RuntimeError:
            if time.monotonic()>=deadline:return None
            time.sleep(.5)


def _rescue_service():
    """Return a body from 0336 after a read-only session lost its channel.

    The broken channel cannot carry auth.stop, so a fresh handle repeats the
    normal start/authenticate/stop exit. Only sessions that sent no write
    command use this; failures leave the camera as it was.
    """
    try:dev=get_device(0x0336)
    except Exception:return False
    try:
        if sys.platform=='darwin':
            from macos_host import claim_camera_interface
            claim_camera_interface(dev)
        auth=SonySenserAuthDevice(GenericUsbDriver(Bounded(dev,20)))
        try:auth.start();auth.authenticate()
        except Exception:pass
        auth.stop()
        print('읽기 전용 통신 오류 후 카메라를 서비스 모드에서 빠져나오게 했습니다(카메라에 쓰지 않음).',flush=True)
        return True
    except Exception as exc:
        print('카메라가 서비스 모드에서 자동으로 빠져나오지 못했습니다(%s). USB를 뽑고 배터리를 약 1분간 분리한 뒤 다시 켜세요.'%exc,flush=True)
        return False
    finally:usb.util.dispose_resources(dev)


class Session:
    def __init__(self,expected=None,media_released=False):
        self.expected=expected;self.healthy=True;self.media_released=media_released
        # Any command that can change camera state disables automatic rescue.
        self.mutating=False
    def __enter__(self):
        global _service_exit
        for attempt in range(1,SERVICE_ATTEMPTS+1):
            _cool_down()
            if sys.platform=='darwin' and (attempt>1 or not self.media_released):
                # A bounce back to MSC lets macOS remount the camera storage.
                from macos_host import unmount_camera
                unmount_camera()
            dev=get_device(0x0e8a)
            try:
                if sys.platform=='darwin':
                    # MDHan's order: verify the MSC interface and claim it first.
                    from macos_host import claim_camera_interface
                    claim_camera_interface(dev,interface_class=(8,6,0x50))
                # Standard USB strings remain usable when the storage LUN and
                # DevInfoSender_GetModelInfo are busy. Neither is a service gate.
                model=usb.util.get_string(dev,dev.iProduct) if dev.iProduct else None
                if model!=core.MODEL:raise RuntimeError('지원 대상: '+core.MODEL+'; USB 모델 불일치')
                auth=SonySenserAuthDevice(GenericUsbDriver(Bounded(dev,20)))
                auth.start();auth.authenticate()
                # An authentication error is not retried or followed by stop.
            finally:usb.util.dispose_resources(dev)
            seen=[]
            self.dev=_wait_for(0x0336,SERVICE_WAIT,seen,prepare=SERVICE_DRIVER_SETUP)
            if self.dev is not None:break
            if '0336' in seen:
                raise RuntimeError('서비스 ID 0336이 나타났지만 인터페이스를 열 수 없습니다. 드라이버와 USB 로그를 보존하고, 다시 인증하지 마세요.')
            # No service identity ever appeared, so no service session exists
            # and nothing was sent to the camera after authentication.
            print('인증 후 카메라가 서비스 모드로 진입하지 않았습니다(그동안 나타난 Sony USB ID: %s).'%(', '.join(seen) or '없음'),flush=True)
            if attempt==SERVICE_ATTEMPTS:
                raise RuntimeError('서비스 ID가 나타나지 않았습니다. 처음이라면 문서에 따라 0336 드라이버를 설치하세요(%d회 시도)'%attempt)
            if _wait_for(0x0e8a,60,seen) is None:
                raise RuntimeError('서비스 ID가 나타나지 않았고, 카메라도 일반 USB로 돌아오지 않았습니다. 카메라에서 「대용량 저장장치(MSC)」를 선택한 뒤 다시 연결하세요')
            _service_exit=time.monotonic()
            print('카메라가 일반 USB로 돌아왔습니다. 잠시 대기 후 자동으로 재시도합니다(%d/%d회).'%(attempt+1,SERVICE_ATTEMPTS),flush=True)
        time.sleep(2)
        if sys.platform=='darwin':
            from macos_host import claim_camera_interface
            claim_camera_interface(self.dev)
        self.backend=Bounded(self.dev)
        self.auth=SonySenserAuthDevice(GenericUsbDriver(self.backend))
        authenticated=False
        try:
            self.auth.start();self.auth.authenticate()
            authenticated=True
            self.channel=SonySenserDevice(self.auth.driver)
            self.camera=SonySenserCamera(self.channel)
            snapshot=self.call(self.camera.getBackupPresetData,True)
            self.identity=service_identity(snapshot,model)
            if self.expected and self.identity!=self.expected:raise RuntimeError('현재 카메라 본체가 백업한 본체와 다릅니다')
        except BaseException:
            try:
                if authenticated and self.healthy:self.auth.stop();_service_exit=time.monotonic()
            finally:usb.util.dispose_resources(self.dev)
            raise
        return self
    def __exit__(self,*args):
        global _service_exit
        stopped=False
        try:
            if self.healthy:self.auth.stop();stopped=True
        finally:usb.util.dispose_resources(self.dev)
        if not self.healthy and not self.mutating:
            stopped=_rescue_service()
        if stopped and sys.platform=='darwin':
            from macos_host import wait_normal_return
            # Only enumerate here. Reading configuration opens a new handle
            # before the next session can release remounted camera storage.
            wait_normal_return(lambda:usb.core.find(idVendor=0x054c,idProduct=0x0e8a,
                backend=usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)))
        if stopped:_service_exit=time.monotonic()
    def call(self,method,*args):
        if not self.healthy:raise RuntimeError('세션 결과를 알 수 없어 이후 명령을 중지합니다')
        if getattr(method,'__name__','') in ('writeBackup','saveBackup'):self.mutating=True
        self.backend.reset()
        try:return method(*args)
        except BaseException:self.healthy=False;raise
    def jiritsu(self,cmd,data=b'',missing=False):
        if cmd not in [15,16,17,18]:raise ValueError('허용되지 않은 Jiritsu 명령입니다')
        if cmd in (16,18):self.mutating=True
        response,out=self.call(self.channel.sendSenserPacket,0x40,struct.pack('<HH',0x1001,cmd)+data)
        if response or len(out)<4:raise RuntimeError('Jiritsu 응답 형식 오류')
        status=struct.unpack_from('<I',out)[0]
        if missing and status==0x10000006:return None
        if status:raise RuntimeError(f'Jiritsu 상태 {status:#x}')
        return out[4:]
    def select(self,path):
        if not path.startswith('/tmp/jiritsu/') or '\0' in path or len(path)>510:raise ValueError('경로 불일치')
        self.jiritsu(15,b'\0'+path.encode('ascii')+b'\0')
    def read(self,path,missing=False):self.select(path);return self.jiritsu(17,b'\0\xff\0',missing)
    def temp(self,name,data=None,remove=False):
        if name not in core.NATIVE_PATHS:raise ValueError('지정된 임시 파일이 아닙니다')
        path='/tmp/jiritsu/'+name;self.select(path)
        if remove:self.jiritsu(18,b'\0\xff')
        elif data:
            if len(data)>65536:raise ValueError('임시 파일이 너무 큽니다')
            self.jiritsu(16,b'\0\xff\0'+data)
        else:raise ValueError('빈 파일 쓰기는 허용되지 않습니다')
    def cp_read(self,key):
        if key not in core.CP_KEYS:raise ValueError('허용되지 않은 CP 유닛입니다')
        return self.call(self.camera._sendAdjustControlPacket,0x603,0x40,struct.pack('<II',key,1))
    def cp_write(self,key,data):
        if key not in core.CP_KEYS or len(data)!={core.CP_KEYS[0]:8192,core.CP_KEYS[1]:28672}[key]:raise ValueError('CP 쓰기 대상 또는 길이 불일치')
        self.mutating=True
        return self.call(self.camera._sendAdjustControlPacket,0x603,0x41,struct.pack('<II',key,1)+data)
    def ap_write(self,key,data):
        if key not in core.AP_KEYS or len(data)!=1:raise ValueError('AP 바인딩 대상 또는 길이 불일치')
        self.mutating=True
        return self.call(self.camera.writeBackup,key,data)


class RecoverySession(Session):
    """Reattach an authenticated service endpoint for a recorded body.

    Acquisition sends no authentication/start/stop. The caller accounts for
    the previous native result and proves the live model before new writes.
    """
    def __enter__(self):
        # Reattaching to an unknown earlier outcome never sends a stop itself.
        self.mutating = True
        if not self.expected or self.expected.get('model') != core.MODEL or not self.expected.get('serial_sha256'):
            raise ValueError('Existing service recovery requires a recorded A7C II body identity')
        self.dev = get_device(0x0336)
        if sys.platform=='darwin':
            from macos_host import claim_camera_interface
            claim_camera_interface(self.dev)
        self.backend = Bounded(self.dev, 30)
        self.auth = SonySenserAuthDevice(GenericUsbDriver(self.backend))
        self.channel = SonySenserDevice(self.auth.driver)
        self.camera = SonySenserCamera(self.channel)
        try:
            snapshot = self.call(self.camera.getBackupPresetData, True)
            self.identity = service_identity(snapshot, core.MODEL)
            if self.identity != {k: self.expected[k] for k in ('model', 'serial_sha256')}:
                raise ValueError('Service endpoint belongs to a different body; no authentication or write attempted')
            self.backend.budget = 180
        except BaseException:
            self.healthy = False
            usb.util.dispose_resources(self.dev)
            raise
        return self
