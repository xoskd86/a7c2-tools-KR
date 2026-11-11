"""Service re-entry cooldown and bounce retry, with simulated USB only."""
from unittest.mock import Mock
import pytest
from looktool import devicesession

transport, _ = devicesession.modules()


class Stop(Exception):
    """Ends the test once the 0336 service session would start."""


class Camera:
    """Fake clock and USB identities; ``bounces`` authentications fail to reach 0336."""
    def __init__(self, monkeypatch, bounces, msc_returns=True, platform='linux'):
        self.now, self.sleeps, self.auths, self.mode = 0.0, [], 0, 'msc'
        self.bounces, self.msc_returns, self.back_at = bounces, msc_returns, None
        monkeypatch.setattr(transport.time, 'monotonic', lambda: self.now)
        monkeypatch.setattr(transport.time, 'sleep', self.sleep)
        monkeypatch.setattr(transport.sys, 'platform', platform)
        monkeypatch.setattr(transport, 'get_device', self.get_device)
        monkeypatch.setattr(transport, '_sony_pids', self.pids)
        monkeypatch.setattr(transport, '_service_exit', None)
        monkeypatch.setattr(transport, 'SERVICE_DRIVER_SETUP', None)
        monkeypatch.setattr(transport.usb.util, 'get_string', lambda dev, index: transport.core.MODEL)
        monkeypatch.setattr(transport.usb.util, 'dispose_resources', lambda dev: None)
        monkeypatch.setattr(transport, 'GenericUsbDriver', lambda backend: backend)
        monkeypatch.setattr(transport, 'Bounded', self.bounded)
        monkeypatch.setattr(transport, 'SonySenserAuthDevice', lambda driver: Mock(authenticate=self.authenticate))
    def sleep(self, seconds):
        self.sleeps.append(seconds); self.now += seconds
    def authenticate(self):
        self.auths += 1
        if self.auths <= self.bounces:
            self.mode, self.back_at = 'bounce', self.now + 5
        else:
            self.mode = 'service'
    def state(self):
        if self.mode == 'bounce' and self.msc_returns and self.now >= self.back_at:self.mode = 'msc'
        return self.mode
    def pids(self):
        return {'msc': ['0e8a'], 'service': ['0336'], 'bounce': ['0d68']}[self.state()]
    def get_device(self, pid):
        if (pid, self.state()) in ((0x0e8a, 'msc'), (0x0336, 'service')):return Mock(iProduct=2)
        raise RuntimeError('absent')
    def bounded(self, dev, *budget):
        if not budget:raise Stop  # the 0336 session backend
        return Mock()


def test_bounce_back_to_msc_is_retried_after_cooldown(monkeypatch, capsys):
    camera = Camera(monkeypatch, bounces=1)
    with pytest.raises(Stop):transport.Session().__enter__()
    out = capsys.readouterr().out
    assert camera.auths == 2
    assert transport.SERVICE_COOLDOWN in [round(s) for s in camera.sleeps]
    assert '0d68' in out and '2/3회' in out


def test_gives_up_after_three_bounces_without_service(monkeypatch):
    camera = Camera(monkeypatch, bounces=3)
    with pytest.raises(RuntimeError, match='3회 시도'):transport.Session().__enter__()
    assert camera.auths == 3


def test_bounce_without_msc_return_is_not_retried(monkeypatch):
    camera = Camera(monkeypatch, bounces=1, msc_returns=False)
    with pytest.raises(RuntimeError, match='일반 USB로 돌아오지 않았습니다'):transport.Session().__enter__()
    assert camera.auths == 1


def test_new_session_waits_out_the_cooldown_after_a_service_exit(monkeypatch):
    camera = Camera(monkeypatch, bounces=0)
    camera.now = 100.0
    monkeypatch.setattr(transport, '_service_exit', 90.0)
    with pytest.raises(Stop):transport.Session().__enter__()
    assert camera.sleeps[0] == pytest.approx(transport.SERVICE_COOLDOWN - 10)
    assert camera.auths == 1


def test_mac_retry_releases_remounted_storage_again(monkeypatch):
    import macos_host
    camera = Camera(monkeypatch, bounces=1, platform='darwin')
    unmounts = []
    monkeypatch.setattr(macos_host, 'unmount_camera', lambda: unmounts.append(camera.now))
    monkeypatch.setattr(macos_host, 'claim_camera_interface', lambda dev, interface_class=None: None)
    with pytest.raises(Stop):transport.Session(media_released=True).__enter__()
    assert camera.auths == 2 and len(unmounts) == 1


def test_service_exit_starts_the_cooldown(monkeypatch):
    camera = Camera(monkeypatch, bounces=0)
    camera.now = 50.0
    session = transport.Session(); session.auth = Mock(); session.dev = Mock()
    session.__exit__(None, None, None)
    assert transport._service_exit == 50.0


def test_seen_service_with_unopenable_interface_does_not_reauthenticate(monkeypatch):
    camera=Camera(monkeypatch,bounces=0)
    get=camera.get_device
    def open_device(pid):
        if pid==0x0336:raise RuntimeError('service driver unavailable')
        return get(pid)
    monkeypatch.setattr(transport,'get_device',open_device)
    with pytest.raises(RuntimeError,match='0336이 나타났지만'):transport.Session().__enter__()
    assert camera.auths==1


def test_windows_driver_preparation_runs_only_after_service_appears(monkeypatch):
    camera=Camera(monkeypatch,bounces=1,platform='win32')
    modes=[]
    monkeypatch.setattr(transport,'SERVICE_DRIVER_SETUP',lambda:modes.append(camera.state()))
    with pytest.raises(Stop):transport.Session().__enter__()
    assert camera.auths==2 and modes==['service']


def test_authentication_error_is_not_retried(monkeypatch):
    camera=Camera(monkeypatch,bounces=0)
    calls=[]
    def fail():calls.append(1);raise RuntimeError('authentication failed')
    monkeypatch.setattr(transport,'SonySenserAuthDevice',lambda driver:Mock(authenticate=fail))
    with pytest.raises(RuntimeError,match='authentication failed'):transport.Session().__enter__()
    assert calls==[1]
