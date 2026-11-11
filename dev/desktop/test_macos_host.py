"""Targeted macOS host preparation, simulated; never opens camera USB."""
import importlib.util
from pathlib import Path
import sys
import types
from unittest.mock import Mock,patch
import pytest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('macos_host_test',ROOT/'src/camera_runtime/macos_host.py')
host=importlib.util.module_from_spec(spec);spec.loader.exec_module(host)


def tree(disks):
    return [dict(IOObjectClass='IOUSBHostDevice',idVendor=0x054c,idProduct=0x0e8a,IORegistryEntryID=77,
                 IORegistryEntryChildren=[dict(IOObjectClass='IOMedia',**{'BSD Name':d}) for d in disks]),
            dict(IOObjectClass='IOUSBHostDevice',idVendor=0x1234,idProduct=1,IORegistryEntryID=5,
                 IORegistryEntryChildren=[dict(IOObjectClass='IOMedia',**{'BSD Name':'disk99s1'})])]


class Mac:
    """Fake ioreg/diskutil for camera disks such as disk9 (card) and disk10 (PMHOME)."""
    def __init__(self,mounts,returncode=0,stderr='',stays_mounted=False):
        self.mounts=dict(mounts)
        self.disks=sorted(set(self.mounts)|{self.whole(d) for d in self.mounts})
        self.returncode,self.stderr,self.stays_mounted=returncode,stderr,stays_mounted
        self.commands,self.unmounts=[],[]
    @staticmethod
    def whole(disk):return 'disk'+disk[4:].split('s')[0]
    def plist(self,*args,**kwargs):
        self.commands.append(args)
        if 'ioreg' in args[0]:return tree(self.disks)
        disk=args[-1].removeprefix('/dev/')
        info=dict(DeviceIdentifier=disk,Internal=False,BusProtocol='USB',
                  WholeDisk=disk==self.whole(disk))
        if not info['WholeDisk']:info['ParentWholeDisk']=self.whole(disk)
        if self.mounts.get(disk):info['MountPoint']=self.mounts[disk]
        return info
    def run(self,command,**kwargs):
        self.unmounts.append(command)
        if not self.returncode and not self.stays_mounted:
            for disk in list(self.mounts):
                if self.whole(disk)==command[-1].removeprefix('/dev/'):self.mounts[disk]=None
        return Mock(returncode=self.returncode,stdout='unmounted',stderr=self.stderr)
    def patches(self):
        clock=iter(range(0,10**6))
        return (patch.object(host,'plist_command',side_effect=self.plist),
                patch.object(host.subprocess,'run',side_effect=self.run),
                patch.object(host.time,'sleep'),
                patch.object(host.time,'monotonic',side_effect=lambda:next(clock)))


def test_waits_for_stable_disks_then_unmounts_whole_camera_disks_only(capsys):
    fake=Mac({'disk9s1':'/Volumes/Untitled','disk10s1':'/Volumes/PMHOME'})
    a,b,c,d=fake.patches()
    with a,b,c,d:result=host.unmount_camera()
    assert result['unmounted']==['disk10','disk9']
    assert fake.unmounts==[['/usr/sbin/diskutil','unmountDisk','/dev/disk10'],
                           ['/usr/sbin/diskutil','unmountDisk','/dev/disk9']]
    assert all('disk99' not in str(command) for command in fake.commands)
    assert 'unmounted' in capsys.readouterr().out


def test_already_unmounted_camera_is_rechecked_and_allowed():
    fake=Mac({'disk9s1':None})
    a,b,c,d=fake.patches()
    with a,b,c,d:assert host.unmount_camera()['unmounted']==['disk9']


def test_stable_empty_camera_without_sd_card_is_allowed():
    fake=Mac({})
    a,b,c,d=fake.patches()
    with a,b,c,d:assert host.unmount_camera()['unmounted']==[]
    assert fake.unmounts==[]


def test_whole_disk_without_parent_field_is_valid():
    fake=Mac({'disk9':None})
    with patch.object(host,'plist_command',side_effect=fake.plist):
        registry,media=host.camera_media()
    assert registry==77 and media['disk9']['parent']=='disk9'


def test_partition_parent_outside_camera_subtree_is_rejected():
    fake=Mac({'disk9s1':None})
    def plist(*args,**kwargs):
        result=fake.plist(*args)
        if args[-1]=='/dev/disk9s1':result['ParentWholeDisk']='disk99'
        return result
    with patch.object(host,'plist_command',side_effect=plist):
        with pytest.raises(RuntimeError,match='parent'):host.camera_media()


def test_busy_volume_stops_before_capture_with_details(capsys):
    fake=Mac({'disk4s1':'/Volumes/CAMERA'},returncode=1,stderr='in use by Photos')
    a,b,c,d=fake.patches()
    with a,b,c,d,pytest.raises(RuntimeError,match='사진'):host.unmount_camera()
    assert 'in use by Photos' in capsys.readouterr().err


def test_volume_still_mounted_after_unmount_stops_before_capture():
    fake=Mac({'disk4s1':'/Volumes/CAMERA'},stays_mounted=True)
    a,b,c,d=fake.patches()
    with a,b,c,d,pytest.raises(RuntimeError,match='모두 마운트 해제하지 못해'):host.unmount_camera()


def test_failed_claim_reattaches_detached_driver():
    device=Mock();device.is_kernel_driver_active.return_value=True
    with patch('usb.util.claim_interface',side_effect=RuntimeError('busy')),patch('usb.util.dispose_resources') as dispose:
        with pytest.raises(RuntimeError,match='busy'):host.claim_camera_interface(device)
    device.detach_kernel_driver.assert_called_once_with(0)
    device.attach_kernel_driver.assert_called_once_with(0)
    dispose.assert_called_once_with(device)


def test_wrong_interface_class_is_refused_before_detach():
    device=Mock();interface=Mock(bInterfaceClass=0xff,bInterfaceSubClass=0,bInterfaceProtocol=0)
    device.get_active_configuration.return_value={(0,0):interface}
    with patch('usb.util.claim_interface') as claim,patch('usb.util.dispose_resources'):
        with pytest.raises(RuntimeError,match='interface'):host.claim_camera_interface(device,interface_class=(8,6,0x50))
    device.detach_kernel_driver.assert_not_called();claim.assert_not_called()


def test_normal_return_waits_and_prompts_for_msc(capsys):
    found=iter([RuntimeError('absent'),None,Mock()])
    def find():
        value=next(found)
        if isinstance(value,Exception):raise value
        return value
    with patch.object(host.time,'sleep'),patch('usb.util.dispose_resources'):
        assert host.wait_normal_return(find) is True
    assert '대용량 저장장치(MSC)' in capsys.readouterr().out


def test_normal_return_timeout_is_reported_not_raised(capsys):
    clock=iter([0,0,host.NORMAL_RETURN_SECONDS+1])
    with patch.object(host.time,'sleep'),patch.object(host.time,'monotonic',side_effect=lambda:next(clock)):
        assert host.wait_normal_return(lambda:None) is False
    assert '복구되지 않았습니다' in capsys.readouterr().out


def test_normal_return_handles_readonly_enumeration_error(capsys):
    import usb.core
    found=iter([usb.core.USBError('Device disappeared during enumeration'),Mock()])
    def find():
        value=next(found)
        if isinstance(value,Exception):raise value
        return value
    with patch.object(host.time,'sleep'),patch('usb.util.dispose_resources'):
        assert host.wait_normal_return(find) is True
    assert 'Device disappeared' in capsys.readouterr().out


def test_mac_connect_unmounts_before_the_first_usb_open(monkeypatch):
    from looktool import devicesession,usbidentity
    events=[]
    fake_host=types.SimpleNamespace(unmount_camera=lambda:events.append('unmount'),
                                    MSC_PROMPT='',NORMAL_RETURN_SECONDS=60)
    class Session:
        def __init__(self,expected,media_released=False):events.append(('session',media_released))
        def __enter__(self):self.identity={'model':'ILCE-7CM2'};return self
    transport=types.SimpleNamespace(Session=Session,ReadinessFailure=RuntimeError,SonyBusyFailure=RuntimeError,
        libusb_package=types.SimpleNamespace(find_library=None),usb=types.SimpleNamespace(util=Mock()))
    monkeypatch.setitem(sys.modules,'macos_host',fake_host)
    monkeypatch.setattr(devicesession.sys,'platform','darwin')
    monkeypatch.setattr(devicesession,'modules',lambda:(transport,types.SimpleNamespace(MODEL='ILCE-7CM2')))
    monkeypatch.setattr(usbidentity,'capture',lambda t:events.append('usb-open') or 'serial-hash')
    with patch('usb.backend.libusb1.get_backend'),patch('usb.core.find',return_value=Mock()):
        camera=devicesession.Camera()._connect()
    assert events==['unmount','usb-open',('session',True)]
    assert camera.identity['usb_serial_sha256']=='serial-hash'


def test_mac_session_claims_msc_interface_before_reading_strings_and_waits_after_stop(monkeypatch):
    from looktool import devicesession
    transport,_=devicesession.modules()
    import macos_host
    events=[]
    monkeypatch.setattr(transport.sys,'platform','darwin')
    monkeypatch.setattr(transport,'get_device',lambda pid:Mock(iProduct=2))
    monkeypatch.setattr(macos_host,'claim_camera_interface',
                        lambda dev,interface_class=None:events.append(('claim',interface_class)))
    def string(dev,index):
        events.append('string');raise KeyboardInterrupt('stop before authentication')
    monkeypatch.setattr(transport.usb.util,'get_string',string)
    monkeypatch.setattr(transport.usb.util,'dispose_resources',lambda dev:None)
    with pytest.raises(KeyboardInterrupt):transport.Session(media_released=True).__enter__()
    assert events==[('claim',(8,6,0x50)),'string']
    session=transport.Session();session.auth=Mock();session.dev=Mock()
    monkeypatch.setattr(macos_host,'wait_normal_return',lambda find:events.append('wait-msc'))
    session.__exit__(None,None,None)
    session.auth.stop.assert_called_once()
    assert events[-1]=='wait-msc'


def test_registry_with_invalid_xml_characters_still_parses():
    # Field log 38: another device's name carried a control character and
    # plistlib rejected the whole registry before any camera access.
    bad=('<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><array><dict>'
         '<key>USB Product Name</key><string>Dock'+chr(1)+'</string>'
         '<key>idVendor</key><integer>1452</integer></dict></array></plist>').encode()
    with patch.object(host.subprocess,'run',return_value=Mock(returncode=0,stdout=bad,stderr=b'')):
        with pytest.raises(Exception):host.plist_command('/usr/sbin/ioreg')
        assert host.plist_command('/usr/sbin/ioreg',lenient=True)==[{'USB Product Name':'Dock','idVendor':1452}]


def test_camera_behind_hub_is_one_camera():
    camera=tree(['disk9'])[0]
    hub=dict(IOObjectClass='IOUSBHostDevice',idVendor=0x05e3,idProduct=0x0610,IORegistryEntryID=3,
             IORegistryEntryChildren=[camera])
    fake=Mac({'disk9':None})
    def plist(*args,**kwargs):
        return [hub,camera] if 'ioreg' in args[0] else fake.plist(*args)
    with patch.object(host,'plist_command',side_effect=plist):
        registry,media=host.camera_media()
    assert registry==77 and list(media)==['disk9']
