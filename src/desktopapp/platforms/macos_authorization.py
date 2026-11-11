"""macOS administrator rights for explicit camera workers, via Terminal sudo.

Like MDHan's sudo adapter, only camera commands receive root privileges.
The GUI remains the login user. The password goes to sudo in Terminal only.
The recipient's probe reported interface access under Terminal sudo while
the system-dialog route failed. This build tests that host-side difference;
full packaged-camera behavior remains a recipient test.
"""
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
import traceback
from ..paths import data_root,write_json


def required(action):
    if sys.platform!='darwin' or os.geteuid()==0:return False
    from ..jobs import CAMERA_ACTIONS
    return action in CAMERA_ACTIONS


def shell_command(request,uid,gid,environment):
    from ..jobs import worker_command
    argv=worker_command(request)
    argv[argv.index('--worker')]='--privileged-worker'
    argv.extend([str(uid),str(gid)])
    assignments={key:environment[key] for key in ('HOME','USER','LOGNAME','PATH','TMPDIR',
        'A7C2_ZIG_CACHE','A7LOOK_AUTO_SETTINGS') if environment.get(key)}
    assignments.update(A7C2_DESKTOP_DATA=str(data_root()),PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
    assignments['LIBUSB_DEBUG']=environment.get('LIBUSB_DEBUG','4')
    assignments.setdefault('A7C2_ZIG_CACHE',str(data_root()/'compiler-cache'))
    shell=shlex.join(['/usr/bin/env']+[key+'='+value for key,value in assignments.items()]+argv)
    parent=Path(request).parent
    shell+=' > '+shlex.quote(str(parent/'authorization.stdout.log'))
    shell+=' 2> '+shlex.quote(str(parent/'authorization.stderr.log'))
    return shell


def launcher(request,uid,gid,environment):
    """Text of the one-job Terminal script; its exit record goes beside the request."""
    shell=shell_command(request,uid,gid,environment)
    exit_file=shlex.quote(str(Path(request).parent/'authorization.exit'))
    return '\n'.join([
        '#!/bin/bash',
        '# A7C II Tools: one camera operation with administrator rights. Generated per job.',
        'trap '+shlex.quote('echo closed > '+exit_file+'; exit 129')+' HUP',
        "trap '' INT QUIT",
        "printf '%s\\n' 'A7C II Tools: 이번 카메라 작업은 관리자 권한으로 USB를 넘겨받아야 합니다.' "
        "'아래에 Mac 로그인 암호를 입력하세요(입력 내용은 표시되지 않음). 암호는 시스템 sudo에만 전달되며 프로그램은 읽거나 저장하지 않습니다.'",
        '/usr/bin/sudo /bin/sh -c '+shlex.quote(shell),
        'code=$?',
        'echo "$code" > '+exit_file,
        'echo "카메라 작업이 끝났습니다(종료 코드 $code). 결과는 A7C II Tools 창을 기준으로 하세요. 이제 이 터미널 창을 닫아도 됩니다."',
        ''])


def command(request,uid,gid,environment):
    path=Path(request).parent/'authorize-in-terminal.command'
    path.write_text(launcher(request,uid,gid,environment),encoding='utf-8',newline='\n')
    path.chmod(0o700)
    return ['/usr/bin/open','-a','Terminal',str(path)]


def alive(pid):
    try:
        pid=int(pid)
        if pid<=0:return False
        os.kill(pid,0)
    except ProcessLookupError:return False
    except PermissionError:return True
    except (TypeError,ValueError):return False
    return True


def wait_for_worker(status,exit_file):
    """Wait for the root worker's outcome, never for a fixed time.

    Closing Terminal after the worker started does not end it (it ignores
    SIGHUP/SIGINT), so keep following its status while its process lives.
    """
    while True:
        current=json.loads(status.read_text(encoding='utf-8'))
        if current.get('phase') in ('SUCCEEDED','FAILED'):return current
        if current.get('phase')=='RUNNING' and not alive(current.get('pid')):
            return current
        if exit_file.exists() and not (current.get('phase')=='RUNNING' and alive(current.get('pid'))):
            return current
        time.sleep(.5)


def execute(path):
    path=Path(path).resolve();request=json.loads(path.read_text(encoding='utf-8'))
    job=Path(request['job_dir']);job.mkdir(parents=True,exist_ok=True)
    status=job/'status.private.json'
    write_json(status,dict(phase='WAITING_AUTHORIZATION',action=request['action'],camera_io=False))
    with (job/'worker.log').open('a',encoding='utf-8') as log:
        log.write('「터미널」 창에서 Mac 로그인 암호 입력을 기다리는 중입니다. 아직 USB를 넘겨받거나 카메라 작업을 시작하지 않았습니다. 암호 입력 전에 터미널 창을 닫으면 카메라에 플래시하지 않습니다.\n')
    print('열린 「터미널」 창에 Mac 로그인 암호를 입력하세요(입력 내용은 표시되지 않음).',flush=True)
    exit_file=job/'authorization.exit'
    exit_file.unlink(missing_ok=True)
    code=None
    try:
        opened=subprocess.run(command(path,os.getuid(),os.getgid(),os.environ),
                              capture_output=True,text=True,encoding='utf-8',errors='replace')
    except Exception as exc:
        opened=None;detail=repr(exc)
    else:detail=(opened.stdout+'\n'+opened.stderr).strip()
    if opened is not None and opened.returncode==0:
        current=wait_for_worker(status,exit_file)
        code=exit_file.read_text(encoding='utf-8').strip() if exit_file.exists() else None
    else:current=json.loads(status.read_text(encoding='utf-8'))
    write_json(job/'authorization.private.json',dict(terminal_sudo=True,
        exit_code=code,detail=detail,password_stored=False))
    if current.get('phase') in ('SUCCEEDED','FAILED'):
        # The privileged worker owns the actual outcome; never replay it.
        return 0 if current['phase']=='SUCCEEDED' else 2
    extra=job/'authorization.stderr.log'
    if extra.is_file():detail+='\n'+extra.read_text(encoding='utf-8',errors='replace')
    if current.get('phase')=='RUNNING':
        error='관리자 프로세스가 예기치 않게 종료되어 카메라 작업 결과를 알 수 없습니다. 진단 패키지를 내보내고, 방금 한 작업을 반복하지 마세요.'
        with (job/'worker.log').open('a',encoding='utf-8') as log:log.write(error+'\n'+detail+'\n')
        write_json(status,dict(phase='FAILED',action=request['action'],error=error,
            error_type='MacWorkerEnded',authorization_denied=False))
        return 2
    cancelled=code in ('closed','1') or 'incorrect password' in detail
    error=('터미널 인증이 완료되지 않았습니다(창이 닫혔거나 암호가 틀림). 이번 카메라 작업은 시작되지 않았으니 원래 버튼을 다시 누르면 됩니다.' if cancelled else
           '터미널 인증이 완료되지 않아 카메라 작업 시작이 보고되지 않았습니다. 로그를 보존하세요. 시스템 상세: '+detail)
    with (job/'worker.log').open('a',encoding='utf-8') as log:log.write(error+'\n'+detail+'\n')
    write_json(status,dict(phase='FAILED',action=request['action'],error=error,
        error_type='MacAuthorizationCancelled' if cancelled else 'MacAuthorizationError',
        camera_io=False,authorization_denied=True))
    return 2


def return_ownership(request,uid,gid):
    """Return app-created root files to the login user, as in the sudo adapter."""
    args=request.get('arguments',{})
    roots=[data_root(),Path(request['job_dir'])]
    roots.extend(Path(args[key]).expanduser() for key in ('workspace','backup') if args.get(key))
    home=Path.home().resolve();seen=set()
    roots.extend([home/'Library/Application Support/Adobe/Lightroom/Modules/A7LookAuto.lrplugin',
                  home/'Library/Application Support/Adobe/CameraRaw/CameraProfiles/A7Look-Imported'])
    for value in roots:
        if value.is_symlink():continue
        root=value.resolve()
        if root in seen or root==home or len(root.parts)<3:continue
        if root.parts[1] in ('System','Library','usr','bin','sbin','etc','dev') and not root.is_relative_to(home):continue
        if root.parts[1]=='Volumes' and len(root.parts)<=3:continue
        seen.add(root)
        if not root.exists():continue
        for directory,folders,files in os.walk(root,followlinks=False):
            folders[:]=[name for name in folders if not (Path(directory)/name).is_symlink()]
            for path in [Path(directory)]+[Path(directory)/name for name in files]:
                if not path.is_symlink() and path.stat().st_uid==0:
                    os.chown(path,uid,gid,follow_symlinks=False)


def privileged(path,uid,gid):
    if sys.platform!='darwin' or os.geteuid()!=0 or uid<=0 or gid<0:
        raise RuntimeError('카메라 인증 worker는 터미널 sudo로 시작해야 합니다.')
    # Closing the Terminal window or Ctrl-C must not cut a camera write short.
    signal.signal(signal.SIGHUP,signal.SIG_IGN)
    signal.signal(signal.SIGINT,signal.SIG_IGN)
    import pwd
    owner=pwd.getpwuid(uid)
    if gid!=owner.pw_gid:raise ValueError('Authorization owner group differs from login user')
    os.umask(0o022)  # Let the login-user GUI read live status/logs before ownership is returned.
    os.environ['HOME']=owner.pw_dir
    os.environ['USER']=os.environ['LOGNAME']=owner.pw_name
    path=Path(path);request=json.loads(path.read_text(encoding='utf-8'))
    try:
        from ..worker import execute_request
        return execute_request(path)
    finally:
        try:return_ownership(request,uid,gid)
        except Exception:
            with (Path(request['job_dir'])/'worker.log').open('a',encoding='utf-8') as log:
                log.write('카메라 작업은 끝났지만 파일 소유권 반환에 실패했습니다. 이 로그를 보존하세요.\n'+traceback.format_exc())
