"""Informational startup dialog; no implicit USB work or installer consent."""
import sys
from unittest.mock import Mock,patch

from desktopapp import connectionguide
from desktopapp import window


def test_notice_distinguishes_camera_prompt_from_actual_tool_failure():
    for phrase in ('연결된 기기를 확인하십시오','정상입니다','전체 로그','대용량 저장장치','모든 읽기/쓰기가 끝','진단 패키지'):
        assert phrase in connectionguide.TEXT
    with patch.object(window.W.QMessageBox,'information') as popup:
        window.Window.show_connection_notice(Mock())
    popup.assert_called_once()
    assert popup.call_args.args[1:]==(connectionguide.TITLE,connectionguide.TEXT)


def test_gui_start_schedules_notice_after_show_before_any_operation():
    app=Mock();app.exec.return_value=0
    with patch.object(window.W.QApplication,'instance',return_value=app), \
         patch.object(window,'Window') as ctor, \
         patch.object(window.QtCore.QTimer,'singleShot') as timer, \
         patch.object(sys,'argv',['A7C2Tools.exe']):
        assert window.main()==0
    ctor.return_value.show.assert_called_once()
    timer.assert_called_once_with(0,ctor.return_value.show_connection_notice)
    ctor.return_value.launch.assert_not_called()
