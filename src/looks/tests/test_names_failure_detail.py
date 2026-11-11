"""Name-operation failures explain the next safe step in plain words."""
from looktool import checkpointworker as cw

BUILD={'count':68}


def test_restore_out_of_space_reports_progress_and_power_cycle():
    # Field log 37: 37 of 68 names restored before /usr ran out of space.
    v=(b'A7NMR001',1,2,6,68,-28,0,0,0,0,0,517,37,0,0,37,0,0)
    text=cw.names_failure_detail(v,2,BUILD,b'')
    assert '37/68' in text and '전원을 완전히 차단' in text and '남은 부분' in text and '반복하지 마' not in text


def test_preflight_out_of_space_wrote_nothing():
    v=(b'A7NMR001',1,4,2,68,-28,0,0,0,0,0,517,0,0,0,0,0,0)
    assert '이번에는 쓰지 않았습니다' in cw.names_failure_detail(v,4,BUILD,b'')


def test_writable_usr_at_preflight_asks_for_a_cold_restart():
    # Field log 40: /usr was still mounted read-write from an earlier operation.
    v=(b'A7NMR001',1,4,1,68,-30,0,0,-30,0,0,517,0,0,0,0,0,0)
    text=cw.names_failure_detail(v,4,BUILD,b'')
    assert '쓰기 가능 상태로 마운트' in text and '이번에는 쓰지 않았습니다' in text and '전원을 완전히 차단' in text


def test_other_failures_add_no_guess():
    v=(b'A7NMR001',1,2,6,68,-5,0,0,0,0,0,517,3,0,0,3,0,0)
    assert cw.names_failure_detail(v,2,BUILD,b'')==''
