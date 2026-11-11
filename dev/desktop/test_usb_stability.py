"""Read-only program-file soak test retains errors and never executes a worker."""
import json
from unittest.mock import Mock
import pytest
from desktopapp import usbcheck,jobs

AP=bytes(20*1024*1024)
CP=b'\1'*(10*1024*1024)


class Clock:
    """Fake monotonic time, advanced by the fake camera instead of sleeping."""
    def __init__(self):self.now=0.0
    def __call__(self):return self.now


class Camera:
    def __init__(self,values,clock,cost=1.0,connect=0.0,fail_connect=None):
        self.values=iter(values);self.calls=[];self.mark_unknown=Mock();self.clock=clock
        self.cost=cost;self.connect=connect;self.fail_connect=fail_connect
    def __enter__(self):
        self.clock.now+=self.connect
        if self.fail_connect:raise self.fail_connect
        return self
    def __exit__(self,*args):pass
    def read(self,path):
        self.calls.append(path);self.clock.now+=self.cost;value=next(self.values)
        if isinstance(value,Exception):raise value
        return value


def good(rounds):return [AP,CP]*rounds


def load(tmp_path):return json.loads((tmp_path/'usb-stability.private.json').read_text(encoding='utf-8'))


def test_good_connection_reads_both_programs_until_minimum_time_and_rounds(tmp_path):
    clock=Clock();camera=Camera(good(100),clock,cost=5.0)
    report=usbcheck.run(tmp_path,factory=lambda:camera,clock=clock)
    # 10 s per round: 120 s minimum outweighs the 5-round minimum.
    assert report['phase']=='PASSED' and len(report['rounds'])==12
    assert camera.calls==list(usbcheck.PATHS)*12
    assert report['total_bytes']==12*(len(AP)+len(CP)) and report['seconds']>=120
    assert not report['camera_firmware_written'] and not report['camera_settings_written']
    assert not report['native_worker_executed']
    assert '보장하지 않' in report['note']


def test_connection_time_does_not_count_towards_minimum_duration(tmp_path):
    clock=Clock();camera=Camera(good(100),clock,cost=5.0,connect=130.0)
    report=usbcheck.run(tmp_path,factory=lambda:camera,clock=clock)
    assert report['connect_seconds']==130 and report['seconds']==120
    assert len(report['rounds'])==12


def test_fast_connection_still_runs_minimum_rounds(tmp_path):
    clock=Clock();camera=Camera(good(100),clock,cost=0.0)
    report=usbcheck.run(tmp_path,factory=lambda:camera,clock=clock,min_seconds=0)
    assert len(report['rounds'])==usbcheck.MIN_ROUNDS


def test_round_cap_before_minimum_duration_is_incomplete_not_passed(tmp_path):
    clock=Clock();camera=Camera(good(100),clock,cost=0.0)
    with pytest.raises(RuntimeError,match='완료되지 않았'):
        usbcheck.run(tmp_path,factory=lambda:camera,clock=clock,max_rounds=7)
    report=load(tmp_path)
    assert report['phase']=='INCOMPLETE' and len(report['rounds'])==7 and report['failed_round'] is None
    camera.mark_unknown.assert_not_called()


@pytest.mark.parametrize('failure',[TimeoutError('transfer timed out'),OSError('device disconnected'),AP[:-1]+b'X',AP[:1000]],
                         ids=['timeout','disconnect','bytes-differ','truncated'])
def test_bad_connection_stops_at_first_failure_with_original_error(tmp_path,failure):
    clock=Clock();camera=Camera([AP,CP,failure,CP],clock)
    with pytest.raises(RuntimeError,match='2회차'):
        usbcheck.run(tmp_path,factory=lambda:camera,clock=clock)
    report=load(tmp_path)
    assert report['phase']=='FAILED' and report['failed_round']==2 and report['failed_stage']=='reading'
    assert len(camera.calls)==3 and len(report['rounds'])==1
    camera.mark_unknown.assert_called_once()


def test_degraded_link_fails_even_when_bytes_match(tmp_path):
    # 20 MiB in 15 s is about 1.3 MB/s, the range of a USB 1.1 or hub-degraded link.
    clock=Clock();camera=Camera(good(10),clock,cost=15.0)
    with pytest.raises(RuntimeError,match='2.0 MB/s보다 낮습니다'):
        usbcheck.run(tmp_path,factory=lambda:camera,clock=clock)
    camera.mark_unknown.assert_called_once()


def test_slow_but_healthy_link_passes(tmp_path):
    # The 10 MiB file in 4 s is 2.5 MB/s: slow, consistent and complete, so it passes.
    clock=Clock();camera=Camera(good(100),clock,cost=4.0)
    assert usbcheck.run(tmp_path,factory=lambda:camera,clock=clock)['phase']=='PASSED'


def test_connection_failure_is_reported_as_connecting_stage(tmp_path):
    clock=Clock();camera=Camera([],clock,connect=3.0,fail_connect=OSError('service entry failed'))
    with pytest.raises(RuntimeError,match='연결 단계'):
        usbcheck.run(tmp_path,factory=lambda:camera,clock=clock)
    report=load(tmp_path)
    assert report['failed_stage']=='connecting' and report['failed_round'] is None and report['seconds']==0


def test_test_is_an_explicit_camera_action_without_battery_or_flash_gate():
    assert 'usb_stability' in jobs.CAMERA_ACTIONS
    assert 'usb_stability' not in jobs.POWER_ACTIONS
