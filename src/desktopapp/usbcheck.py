"""Read-only USB soak test over the camera's program files; never stages native workers.

The two program files are the same large reads a real write performs during its
full readback. Every round re-reads both and must match the first round byte for
byte. The clock starts once the service connection is ready, so connection and
authentication time never counts towards the minimum duration. A completed read
slower than MIN_MB_PER_SECOND indicates a degraded link (USB 1.1, hub or poor
cable); a read that hangs is stopped by the transport's own timeout and fails.
"""
import time
from pathlib import Path
from .paths import write_json

PATHS=('/usr/lib/appFw.so','/cp/cpapp-b.bin')
MIN_SECONDS=120
MIN_ROUNDS=5
MAX_ROUNDS=60
MIN_MB_PER_SECOND=2.0
MIN_BYTES,MAX_BYTES=1024*1024,256*1024*1024
NOTE='통과는 이 시간 동안 연속 읽기 전용 전송이 안정적이었다는 뜻일 뿐이며, 플래시 중 연결이 끊기지 않는다고 보장하지 않고 케이블 인증을 의미하지도 않습니다.'


class Incomplete(RuntimeError):
    """The round cap was reached before the minimum duration; not a pass."""


def run(job,*,factory=None,clock=time.monotonic,min_seconds=MIN_SECONDS,min_rounds=MIN_ROUNDS,
        max_rounds=MAX_ROUNDS,min_rate=MIN_MB_PER_SECOND):
    if factory is None:
        from looktool.devicesession import Camera
        factory=Camera
    job=Path(job);job.mkdir(parents=True,exist_ok=True)
    report=dict(schema='a7c2-usb-stability/3',phase='CONNECTING',paths=list(PATHS),rounds=[],
                minimum_seconds=min_seconds,minimum_rounds=min_rounds,maximum_rounds=max_rounds,
                minimum_mb_per_second=min_rate,camera_firmware_written=False,camera_settings_written=False,
                native_worker_executed=False,note=NOTE)
    def save():write_json(job/'usb-stability.private.json',report)
    save();connect_begin=clock();start=None;number=0
    def elapsed():return 0.0 if start is None else clock()-start
    try:
        with factory() as camera:
            start=clock();report.update(phase='READING',connect_seconds=round(start-connect_begin,3));save()
            baseline={}
            while number<min_rounds or elapsed()<min_seconds:
                if number>=max_rounds:
                    raise Incomplete('%d회 읽어 횟수 상한에 도달했지만 연속 읽기가 %.0f초만 지속되어 %d초에 못 미쳤습니다. 이번에는 통과로 보지 않습니다'%
                                     (number,elapsed(),min_seconds))
                number+=1
                print('USB 안정성 검사: %d회차 프로그램 파일 읽기 전용 읽기(최소 %d회, 연속 %d초). 펌웨어나 설정에는 쓰지 않습니다.'%
                      (number,min_rounds,min_seconds),flush=True)
                reads=[]
                for path in PATHS:
                    begin=clock()
                    try:
                        raw=camera.read(path)
                        seconds=clock()-begin
                        if raw is None or not MIN_BYTES<=len(raw)<=MAX_BYTES:
                            raise ValueError('프로그램 파일 전체 리드백이 되지 않았습니다: '+path)
                        if path in baseline and raw!=baseline[path]:
                            raise ValueError('반복 리드백한 바이트가 일치하지 않습니다: '+path)
                        rate=len(raw)/1048576/seconds if seconds>0 else None
                        if rate is not None and rate<min_rate:
                            raise ValueError('%s 읽기 속도가 %.2f MB/s로 %.1f MB/s보다 낮습니다. USB 1.1, 도킹 스테이션 또는 품질이 낮은 케이블을 거치고 있을 수 있습니다'%
                                             (path,rate,min_rate))
                    except BaseException:
                        mark=getattr(camera,'mark_unknown',None)
                        if mark:mark()
                        raise
                    baseline.setdefault(path,raw)
                    reads.append(dict(path=path,bytes=len(raw),seconds=round(seconds,3),
                                      mb_per_second=round(rate,2) if rate is not None else None))
                report['rounds'].append(dict(round=number,bytes=sum(r['bytes'] for r in reads),reads=reads,passed=True))
                save()
            seconds=elapsed()
        speeds=[r['mb_per_second'] for row in report['rounds'] for r in row['reads'] if r['mb_per_second']]
        report.update(phase='PASSED',total_bytes=sum(r['bytes'] for r in report['rounds']),
                      seconds=round(seconds,3),camera_io_ended=True,
                      slowest_mb_per_second=min(speeds) if speeds else None,
                      fastest_mb_per_second=max(speeds) if speeds else None)
        save()
        print('%d회 읽기 전용 전송을 모두 마쳤습니다. 총 %.1f MB, 연속 읽기 %.0f초, 회차별 바이트 일치. %s'%
              (len(report['rounds']),report['total_bytes']/1048576,report['seconds'],NOTE),flush=True)
        return report
    except BaseException as exc:
        incomplete=isinstance(exc,Incomplete)
        connected=start is not None
        report.update(phase='INCOMPLETE' if incomplete else 'FAILED',
                      failed_stage='reading' if connected else 'connecting',
                      failed_round=number if connected and not incomplete else None,
                      error_type=type(exc).__name__,error=str(exc),camera_io_ended=True,seconds=round(elapsed(),3))
        save()
        if incomplete:
            raise RuntimeError('USB 안정성 검사가 완료되지 않았습니다: %s. 이번에는 펌웨어나 설정에 쓰지 않았습니다.'%str(exc)) from exc
        raise RuntimeError('USB 안정성 검사를 통과하지 못했습니다(%s): %s. 케이블을 바꾸거나 컴퓨터 USB 포트에 직접 연결한 뒤 다시 검사하세요. 이번에는 펌웨어나 설정에 쓰지 않았습니다.'%
                           ('%d회차'%number if connected else '연결 단계',str(exc))) from exc
