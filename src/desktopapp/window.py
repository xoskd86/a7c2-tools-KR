"""Six explicit workflows, background workers and a complete diagnostic log."""
from datetime import datetime
import json
from pathlib import Path
import sys
import uuid
from PySide6 import QtCore, QtGui, QtWidgets as W
from . import VERSION
from . import jobs
from . import presets
from . import reloadguide

DRIVER_HINT = ('\n드라이버 또는 연결에 실패했습니다: 먼저 "연결 및 드라이버" 페이지에서 드라이버를 제거한 뒤 다시 설치하세요. 그래도 안 되면 '
               '같은 페이지의 "Zadig로 드라이버 수동 설치"를 눌러 안내에 따라 0E8A와 0336 두 ID에 각각 libusbK를 설치하세요.')
from . import connectionguide
from .paths import ROOT, data_root, load_settings, write_json
from .theme import WINDOW as STYLE


class Window(W.QMainWindow):
    def __init__(self, *, demo=False):
        super().__init__()
        self.setWindowTitle('A7C II Tools · Desktop Preview')
        self.resize(1130, 850)
        self.setMinimumSize(960, 730)
        self.settings = load_settings()
        self.fields, self.buttons = {}, []
        self.current_job = None
        self.log_offset = 0
        self.process = None
        self.demo = demo
        self.camera_operation = False
        self.setStyleSheet(STYLE)
        central = W.QWidget()
        self.setCentralWidget(central)
        layout = W.QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        self.nav = W.QListWidget()
        self.nav.setFixedWidth(205)
        self.nav.addItems(['홈 · 원클릭 설치', '연결 및 드라이버', '공장 기능 복원', 'FL2 / FL3', '커스텀 크리에이티브 룩', '세로 표시', '전자 셔터음'])
        layout.addWidget(self.nav)
        right = W.QVBoxLayout()
        right.setContentsMargins(22, 18, 22, 16)
        layout.addLayout(right, 1)
        heading = W.QHBoxLayout()
        title = W.QLabel('A7C II Tools')
        title.setStyleSheet('font-size:25px;font-weight:650;')
        heading.addWidget(title)
        heading.addStretch()
        heading.addWidget(W.QLabel('펌웨어 버전'))
        self.firmware = W.QComboBox()
        self.firmware.addItems(['2.01'])
        heading.addWidget(self.firmware)
        right.addLayout(heading)
        self.description = W.QLabel(('Windows 테스트 버전' if sys.platform == 'win32' else '데스크톱 개발 버전') + ' · 원클릭 설치와 6가지 기능 · ' + VERSION)
        self.description.setProperty('hint', True)
        right.addWidget(self.description)
        self.pages = W.QStackedWidget()
        right.addWidget(self.pages, 1)
        self.home_page()
        self.connection_page()
        self.recovery_page()
        self.fl23_page()
        self.look_page()
        self.vertical_page()
        self.sound_page()
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self.progress = W.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        right.addWidget(self.progress)
        self.status = W.QLabel('준비 완료. 왼쪽에서 기능을 선택하세요. 프로그램을 실행하는 것만으로는 카메라에 접근하지 않습니다.')
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        self.log = W.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(180)
        self.log.setPlaceholderText('전체 실행 로그와 원본 오류가 여기에 표시됩니다.')
        right.addWidget(self.log)
        bottom = W.QHBoxLayout()
        open_log = W.QPushButton('작업 폴더 열기')
        open_log.clicked.connect(self.open_job)
        bottom.addWidget(open_log)
        export = W.QPushButton('진단 패키지 내보내기')
        export.clicked.connect(self.export_logs)
        bottom.addWidget(export)
        bottom.addStretch()
        self.stop = W.QPushButton('오프라인 작업 중지')
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.cancel_offline)
        bottom.addWidget(self.stop)
        right.addLayout(bottom)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.poll_job)
        self.timer.start(250)

    def page(self, title, detail):
        wrapper = W.QScrollArea()
        wrapper.setWidgetResizable(True)
        body = W.QWidget()
        layout = W.QVBoxLayout(body)
        layout.setContentsMargins(0, 8, 8, 8)
        label = W.QLabel(title)
        label.setStyleSheet('font-size:20px;font-weight:600;')
        layout.addWidget(label)
        hint = W.QLabel(detail)
        hint.setWordWrap(True)
        hint.setProperty('hint', True)
        layout.addWidget(hint)
        wrapper.setWidget(body)
        self.pages.addWidget(wrapper)
        return layout

    def box(self, layout, title):
        group = W.QGroupBox(title)
        form = W.QFormLayout(group)
        form.setFieldGrowthPolicy(W.QFormLayout.AllNonFixedFieldsGrow)
        layout.addWidget(group)
        return form

    def advanced(self, layout, title):
        toggle = W.QToolButton()
        toggle.setText(title)
        toggle.setCheckable(True)
        toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        toggle.setArrowType(QtCore.Qt.RightArrow)
        content = W.QWidget()
        content.setVisible(False)
        inner = W.QVBoxLayout(content)
        inner.setContentsMargins(0, 0, 0, 0)
        toggle.toggled.connect(content.setVisible)
        toggle.toggled.connect(lambda open: toggle.setArrowType(QtCore.Qt.DownArrow if open else QtCore.Qt.RightArrow))
        layout.addWidget(toggle)
        layout.addWidget(content)
        return inner

    def direct_args(self, group, **values):
        work = self.work(group)
        return dict(self.common(), workspace=work, **{key: value for key, value in values.items() if key != 'mode'})

    def setup_args(self):
        root = self.settings.get('setup_work', '')
        state = Path(root, 'setup.private.json') if root else None
        if state is not None and state.is_file():
            saved = json.loads(state.read_text(encoding='utf-8'))
            if saved['phase'] != 'COMPLETE':
                return dict(self.common(), workspace=root, options=saved['options'])
        root = self.work('setup')
        tool = ROOT / ('desktop-assets/exiftool/exiftool.exe' if sys.platform=='win32' else 'desktop-assets/exiftool/exiftool')
        looks = [dict(slot=s, path=self.text('setup_look_' + str(s))) for s in range(1, 7) if self.text('setup_look_' + str(s))]
        options = dict(fl23=self.setup_fl23.isChecked(), sound=self.setup_sound.isChecked(),
            vertical=self.setup_vertical.isChecked(), plugin=self.setup_plugin.isChecked(),
            exiftool=str(tool), looks=[] if self.setup_presets.isChecked() else looks,
            preset_pack=presets.PACK if self.setup_presets.isChecked() else None)
        return dict(self.common(), workspace=root, options=options)

    def home_page(self):
        layout = self.page('원클릭 설치', '룩 6종, FL2／FL3, 전자 셔터음, 세로 표시 저장 스위치를 한 번에 설치합니다. 안내에 따라 전원을 완전히 끄고 다시 켠 뒤에는 같은 버튼이 로드만 확인합니다. 슬롯별로 설치할 필요가 없습니다.')
        self.setup_presets = W.QCheckBox('내장 룩 6종 설치(커스텀 1–6을 교체, 기존 크리에이티브 룩은 유지)')
        self.setup_presets.setChecked(True)
        self.setup_fl23 = W.QCheckBox('FL2／FL3 활성화'); self.setup_fl23.setChecked(True)
        self.setup_sound = W.QCheckBox('Sony 네이티브 전자 셔터음 활성화'); self.setup_sound.setChecked(True)
        self.setup_vertical = W.QCheckBox('세로 촬영 정보 표시를 켬으로 저장(재시작 후 자동 적용)'); self.setup_vertical.setChecked(True)
        self.setup_plugin = W.QCheckBox('Lightroom 자동 인식 플러그인 설치(Lightroom 필요)')
        prior_path=Path(self.settings.get('setup_work',''),'setup.private.json')
        prior=json.loads(prior_path.read_text(encoding='utf-8')) if prior_path.is_file() else {}
        if prior and prior.get('phase')!='COMPLETE':
            options=prior['options']
            self.setup_presets.setChecked(options.get('preset_pack') == presets.PACK)
            for box,key in ((self.setup_fl23,'fl23'),(self.setup_sound,'sound'),(self.setup_vertical,'vertical'),(self.setup_plugin,'plugin')):
                box.setChecked(bool(options.get(key,key!='plugin')))
            for box in (self.setup_presets,self.setup_fl23,self.setup_sound,self.setup_vertical,self.setup_plugin):box.setEnabled(False)

        self.setup_note=W.QLabel('설치가 아직 끝나지 않았습니다. "설치 계속"을 누르고 팝업에 따라 다음 단계를 진행하세요. 전원 차단이 필요한 경우에도 USB를 다시 연결한 뒤 이 버튼을 누르세요.')
        self.setup_note.setWordWrap(True)
        self.setup_note.setVisible(bool(prior) and prior.get('phase')!='COMPLETE')
        layout.addWidget(self.setup_note)
        layout.addWidget(self.setup_presets)
        self.setup_preset_summary = W.QLabel(presets.SUMMARY)
        self.setup_preset_summary.setWordWrap(True)
        if prior and prior.get('phase')!='COMPLETE':
            names = ['%s %s' % (r['slot'], r.get('name', Path(r['path']).stem)) for r in prior['options'].get('looks', [])]
            self.setup_preset_summary.setText('이번에 저장된 룩: ' + (' · '.join(names) if names else '룩을 선택하지 않아 6종을 추가하지 않습니다'))
        else:
            self.setup_preset_summary.setEnabled(self.setup_presets.isChecked())
            self.setup_presets.toggled.connect(self.setup_preset_summary.setEnabled)
        layout.addWidget(self.setup_preset_summary)
        for check in (self.setup_fl23, self.setup_sound, self.setup_vertical, self.setup_plugin):layout.addWidget(check)
        self.setup_button=self.button(layout, '새 원클릭 설치 시작' if prior.get('phase')=='COMPLETE' else '설치 계속' if prior else '원클릭 설치',
                                      'setup_all', self.setup_args, primary=True)
        self.setup_result_button=W.QPushButton('지난 설치 결과 보기(카메라 연결 안 함)')
        self.setup_result_button.clicked.connect(self.show_setup_result)
        self.setup_result_button.setVisible(prior.get('phase')=='COMPLETE')
        layout.addWidget(self.setup_result_button);self.buttons.append(self.setup_result_button)
        look_section=self.advanced(layout,'직접 만든 A7Look 파일 사용(먼저 내장 6종 체크 해제)')
        custom_container = self.setup_custom_container = look_section.parentWidget()
        custom_container.setEnabled(not self.setup_presets.isChecked() and not (prior and prior.get('phase')!='COMPLETE'))
        self.setup_presets.toggled.connect(lambda enabled: custom_container.setEnabled(not enabled))
        form = self.box(look_section, 'A7Look 파일 선택')
        for slot in range(1, 7):
            self.field(form, 'setup_look_' + str(slot), '커스텀 ' + str(slot), kind='file', filter='Compiled looks (*.a7look)')
        compile_button = W.QPushButton('DCP／LUT로 A7Look 생성…')
        compile_button.clicked.connect(self.compiler_dialog); look_section.addWidget(compile_button); self.buttons.append(compile_button)
        hint = W.QLabel('공장 초기화, 드라이버 제거, 룩 삭제는 설치 과정에 포함되지 않습니다. 커스텀 사운드는 "전자 셔터음"에서 변환한 뒤 바로 저장하세요. 미리 듣기는 선택입니다.')
        hint.setWordWrap(True); layout.addWidget(hint); layout.addStretch()

    def drivers_first(self, arguments):
        """Suggest the connection page before a first one-click install on Windows."""
        if (sys.platform != 'win32' or self.settings.get('connection_verified')
                or Path(arguments.get('workspace', ''), 'setup.private.json').is_file()):
            return True
        choice = self.ask_drivers_first()
        if choice == 'drivers':
            self.nav.setCurrentRow(1)
        return choice == 'continue'

    def ask_drivers_first(self):
        box = W.QMessageBox(self)
        box.setWindowTitle('드라이버를 먼저 설치하는 것을 권장합니다')
        box.setText('이 컴퓨터는 아직 카메라에 연결된 적이 없습니다. 먼저 "연결 및 드라이버" 페이지에서 "2단계 드라이버 준비 및 연결 테스트"를 누르고, '
                    '연결 성공을 확인한 뒤 돌아와서 원클릭 설치를 하는 것을 권장합니다.')
        box.setInformativeText('원클릭 설치도 드라이버 설치를 자동으로 시도하지만, 드라이버가 실패하면 문제 해결이 더 번거롭습니다. '
                               '드라이버가 계속 설치되지 않으면 해당 페이지의 "Zadig로 드라이버 수동 설치"를 눌러 안내에 따르세요.')
        go = box.addButton('드라이버 먼저 설치', W.QMessageBox.AcceptRole)
        box.addButton('바로 원클릭 설치', W.QMessageBox.DestructiveRole)
        cancel = box.addButton(W.QMessageBox.Cancel)
        box.setDefaultButton(go)
        box.exec()
        return 'drivers' if box.clickedButton() is go else 'cancel' if box.clickedButton() is cancel else 'continue'

    def driver_step_failed(self, action):
        if action in ('usb_prepare', 'connect', 'driver_install'):
            return True
        if action != 'setup_all':
            return False
        try:
            state = json.loads(Path(self.settings.get('setup_work', ''), 'setup.private.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return False
        return 'drivers' not in state.get('completed_steps', [])

    def direct_verify_args(self, group, accepted=False):
        workspace=self.settings.get(group + '_direct_work', '')
        args={'workspace': workspace, 'accepted': accepted}
        if group=='factory':args.update(factory_verify=True,factory_preferred=self.text('factory_failed_work'))
        return args

    def show_setup_result(self):
        from .completion import archive_error,text
        path=Path(self.settings.get('setup_work',''),'setup.private.json')
        if not path.is_file():return
        state=json.loads(path.read_text(encoding='utf-8'))
        if state.get('phase')!='COMPLETE':
            W.QMessageBox.information(self,'설치가 아직 끝나지 않았습니다','홈의 "설치 계속"을 누르고 안내에 따라 남은 단계를 완료하세요.')
            return
        if archive_error(state):write_json(path,state)
        W.QMessageBox.information(self,'설치 완료',text(state))

    def refresh_setup_controls(self):
        path=Path(self.settings.get('setup_work',''),'setup.private.json')
        state=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        pending=bool(state) and state.get('phase')!='COMPLETE'
        self.setup_button.setText('설치 계속' if pending else '새 원클릭 설치 시작' if state else '원클릭 설치')
        self.setup_result_button.setVisible(state.get('phase')=='COMPLETE')
        self.setup_note.setVisible(pending)
        self.setup_custom_container.setEnabled(not pending and not self.setup_presets.isChecked() and self.process is None)
        if not state:
            self.setup_preset_summary.setText(presets.SUMMARY)
            self.setup_preset_summary.setEnabled(self.setup_presets.isChecked())
        for box in (self.setup_presets,self.setup_fl23,self.setup_sound,self.setup_vertical,self.setup_plugin):
            box.setEnabled(not pending and self.process is None)

    def clear_installation_state(self, verified=True):
        # Clear active navigation/resume pointers, not diagnostic evidence or
        # user source files. A verified factory reset invalidates these plans.
        # Before verification only the sound records are retired: the restore
        # has already returned every sound program window to stock.
        if verified:
            for key in ('setup_work','fl23_direct_work','look_direct_work',
                        'look_resume_source','look_job_dir'):
                self.settings.pop(key,None)
            self._look_displayed_pending=None
            self.look_editor.set_operations([])
            self.fl23_accept.setChecked(False)
        self.sound_accept.setChecked(False)
        # The old sound workspace describes the pre-reset camera installation.
        # Start fresh while retaining its files and the selected source clip.
        sound=self.work('sound')
        for key,value in (('sound_work',sound),('sound_backup',sound+'-backup')):
            self.fields[key].setText(value);self.settings[key]=value
        write_json(data_root()/'desktop.json',self.settings)
        self.log.appendPlainText('공장 복원 확인을 통과했습니다. 이전 설치 상태를 지웠습니다. 과거 진단 로그는 보존되며, 새 설치는 처음부터 시작합니다.' if verified else
                                 '공장 복원 쓰기가 끝났습니다. 이전 사운드 기록은 비활성화되었고(파일은 보존), 이후 사운드 작업은 더 이상 영향을 받지 않습니다.')

    def reload_controls(self, layout, checks):
        guide = W.QPushButton('전원을 완전히 끄고 다시 켜는 방법은? 자세한 단계 보기')
        guide.clicked.connect(lambda: self.show_reload_guide())
        layout.addWidget(guide)
        self.buttons.append(guide)
        confirm = W.QCheckBox('USB를 뽑고, 램프가 꺼질 때까지 기다린 뒤 전원을 끄고, 배터리를 최소 1분간 분리했다가 다시 켰습니다.\n확인함: ' + checks)
        confirm.setToolTip(reloadguide.text())
        layout.addWidget(confirm)
        return confirm

    def show_reload_guide(self, *, setup=False, mode=None):
        title = ('복원이 아직 시작되지 않았습니다. 전원을 차단한 뒤 다시 시도하세요' if mode=='factory_prewrite_temporary' else
                 '복원이 아직 끝나지 않았습니다. 재시작 후 확인하세요' if mode=='factory_reset' else
                 '미리 듣기 결과가 기록되었습니다. 전원을 차단해 원래 소리로 복원하세요' if mode=='sound_confirm' else '읽기/쓰기가 끝났습니다. 전원을 완전히 끄고 다시 켜세요')
        W.QMessageBox.information(self, title, reloadguide.text(setup=setup, mode=mode))

    def show_connection_notice(self):
        W.QMessageBox.information(self,connectionguide.TITLE,connectionguide.TEXT)

    def field(self, form, key, label, *, kind=None, filter='All files (*)', value=''):
        line = W.QLineEdit(str(self.settings.get(key, value)))
        line.setObjectName(key)
        self.fields[key] = line
        if kind:
            row = W.QHBoxLayout()
            row.addWidget(line, 1)
            button = W.QPushButton('선택…')
            button.clicked.connect(lambda: self.browse(line, kind, filter))
            row.addWidget(button)
            form.addRow(label, row)
        else:
            form.addRow(label, line)
        return line

    def browse(self, field, kind, filter):
        if kind == 'folder':
            path = W.QFileDialog.getExistingDirectory(self, '폴더 선택', field.text())
        elif kind == 'save':
            path = W.QFileDialog.getSaveFileName(self, '파일 저장', field.text(), filter)[0]
        else:
            path = W.QFileDialog.getOpenFileName(self, '파일 선택', field.text(), filter)[0]
        if path:
            field.setText(path)

    def button(self, layout, text, action, arguments=lambda: {}, *, primary=False):
        button = W.QPushButton(text)
        button.setObjectName('action_' + action)
        button.setProperty('primary', primary)
        button.clicked.connect(lambda: self.launch(action, arguments()))
        layout.addWidget(button)
        self.buttons.append(button)
        return button

    def text(self, key):
        return self.fields[key].text().strip()

    def common(self):
        return {'firmware': self.firmware.currentText()}

    def work(self, label):
        return str(data_root() / 'work' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + label + '-' + uuid.uuid4().hex[:5]))

    def connection_page(self):
        layout = self.page('연결 및 드라이버', '카메라 전원을 켜고 "대용량 저장장치／Mass Storage"를 선택한 뒤, 데이터 전송이 가능한 USB 케이블을 사용하세요. 한 번에 카메라 한 대만 연결하세요.')
        form = self.box(layout, 'USB 연결')
        self.driver_identity = W.QComboBox()
        self.driver_identity.addItems(['일반 연결 · 054C:0E8A', '서비스 연결 · 054C:0336'])
        form.addRow('드라이버 대상', self.driver_identity)
        if sys.platform == 'darwin':
            self.field(form, 'mac_volume', 'macOS 카메라 저장 볼륨', kind='folder', value='/Volumes/')
        self.button(layout, '카메라 및 드라이버 검사', 'driver_scan', primary=True)
        if sys.platform == 'win32':
            # Manual Zadig installation sits beside the automatic one: many
            # computers where the automatic install fails succeed with it.
            drivers = W.QHBoxLayout()
            layout.addLayout(drivers)
            self.button(drivers, '2단계 드라이버 준비 및 연결 테스트(자동)', 'usb_prepare', primary=True)
            zadig = W.QPushButton('Zadig로 드라이버 수동 설치(자동 실패 시)')
            zadig.setObjectName('zadig_guide')
            zadig.setProperty('primary', True)
            zadig.clicked.connect(self.show_zadig_guide)
            drivers.addWidget(zadig)
            self.buttons.append(zadig)
        else:
            self.button(layout, 'USB 서비스 연결 준비', 'usb_prepare', primary=True)
        self.button(layout, '선택한 ID의 드라이버만 설치(고급)' if sys.platform == 'win32'
                    else 'USB 접근 확인(macOS는 Zadig 불필요)', 'driver_install',
                    lambda: {'pid': ['0e8a', '0336'][self.driver_identity.currentIndex()]})
        if sys.platform == 'darwin':
            self.button(layout, '선택한 카메라 저장 볼륨 꺼내기', 'mac_unmount', lambda: {'volume': self.text('mac_volume')})
        self.button(layout, '서비스 연결 테스트(펌웨어 쓰기 없음)', 'connect')
        self.button(layout, 'USB 연결 안정성 검사(읽기 전용)', 'usb_stability')
        if sys.platform == 'win32':
            self.button(layout, '카메라 드라이버 제거, Windows 기본 연결로 복원', 'driver_uninstall',
                        lambda: {'pids': ['0e8a', '0336']})
        hint = W.QLabel('연결이 끊기면 먼저 전체 로그를 확인하세요. 오류가 나도 다시 플래시하거나 드라이버를 자동 재설치하지 않습니다.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()

    def recovery_page(self):
        layout = self.page('공장 기능 복원', '이 프로젝트가 수정하는 영역의 공장 2.01 기준 데이터가 내장되어 있습니다. 지원되는 프로젝트 수정을 제거하고, 커스텀 슬롯 6개를 ST 기본값으로 설정하며, 카메라 보정값은 유지합니다. 이전 백업이나 전체 펌웨어 다운로드가 필요 없으며, 이전 레시피는 되찾지 않습니다.')
        self.factory_button=self.button(layout, '공장 2.01 기능 복원／복원 계속(이전 백업 불필요)', 'direct_factory_reset',
                    lambda: self.direct_args('factory', mode='factory_reset', factory='builtin'), primary=True)
        self.factory_status=W.QLabel('복원 쓰기 후에는 반드시 팝업에 따라 재시작한 뒤 다시 연결해 확인해야 합니다. 확인을 통과하기 전에는 복원 완료로 표시하지 않습니다.')
        self.factory_status.setWordWrap(True);layout.addWidget(self.factory_status)
        resume_section = self.advanced(layout, '복원 트랜잭션(자동 인식, 수동 선택도 가능)')
        resume_form = W.QFormLayout()
        self.field(resume_form, 'factory_failed_work', '복원 트랜잭션 폴더(보통 자동)', kind='folder',
                   value=self.settings.get('factory_direct_work', ''))
        resume_section.addLayout(resume_form)
        self.factory_accept = self.reload_controls(layout, '화면 표시, 촬영 및 재생 정상')
        self.button(layout, '재시작 후 확인하고 복원 완료(복원 쓰기 후 필수)', 'direct_verify',
                    lambda: self.direct_verify_args('factory', self.factory_accept.isChecked()))
        layout.addWidget(W.QLabel('카메라가 여전히 USB 서비스 통신에 진입할 수 있어야 합니다. 이 작업은 Sony 업데이터를 이용한 다운그레이드 재플래시가 아닙니다.'))
        expert = self.advanced(layout, '전체 공장 2.01 참조 파일 가져오기(고급, 선택)')
        reference = self.box(expert, '공장 파일')
        self.field(reference, 'factory_file', 'Sony BODYDATA.DAT', kind='file', filter='Sony firmware (*.DAT *.bin);;All files (*)')
        self.button(expert, '선택한 공장 파일 준비(카메라 연결 안 함)', 'factory_prepare',
                    lambda: {'source': self.text('factory_file') or str(data_root() / 'factory-2.01')})
        self.button(expert, '가져온 전체 공장 파일로 복원', 'direct_factory_reset',
                    lambda: self.direct_args('factory', mode='factory_reset', factory=str(data_root() / 'factory-2.01')))
        legacy = self.advanced(layout, '이전 백업으로 과거 상태 되돌리기(선택)')
        self.backup_recovery_controls(legacy)
        layout.addStretch()
        self.refresh_factory_controls()

    def refresh_factory_controls(self):
        work=self.settings.get('factory_direct_work') or self.text('factory_failed_work')
        path=Path(work,'direct.private.json') if work else None
        try:state=json.loads(path.read_text(encoding='utf-8')) if path and path.is_file() else {}
        except (OSError,ValueError):return
        phase=state.get('phase')
        if phase=='AWAITING_RELOAD':
            self.factory_button.setText('재시작 후 확인하고 복원 완료')
            self.factory_status.setText('복원이 아직 끝나지 않았습니다: 쓰기와 리드백은 끝났습니다. 전원을 차단해 재시작하고 USB를 다시 연결한 뒤 위의 버튼을 눌러 확인하세요.')
        elif phase in ('LOADED','COMPLETE') and state.get('readonly_after_reload_verified'):
            self.factory_button.setText('공장 2.01 기능 다시 복원(이전 백업 불필요)')
            self.factory_status.setText('지난 복원 확인을 통과했습니다. 프로그램, 설정, 로드 상태와 읽기 전용 마운트를 확인했습니다. 현재 기능과 촬영 검수는 카메라 본체를 기준으로 하세요.')
        elif phase=='NEEDS_REVIEW':
            from looktool.factoryrecover import connection_failed_before_restore,prewrite_temporary_conflict
            if prewrite_temporary_conflict(state):
                self.factory_button.setText('전원 차단 후 복원 재시도(이번에는 아직 쓰지 않음)')
                self.factory_status.setText('이번 복원은 아직 쓰지 않았으며, 카메라 임시 파일 충돌이 발생했습니다. 팝업에 따라 전원을 완전히 차단하고 USB를 다시 연결한 뒤 위의 버튼을 눌러 재시도하세요. 이전 LUT 기록은 필요 없습니다.')
                return
            if connection_failed_before_restore(state):
                self.factory_button.setText('공장 2.01 기능 복원(지난번에는 시작 전 중단)')
                self.factory_status.setText('지난번에는 USB 연결 단계에서 중지되어 복원 쓰기가 시작되지 않았습니다. 위의 버튼을 누르고 시스템 인증을 마친 뒤 다시 연결하세요.')
                return
            self.factory_button.setText('복원 계속／미완료 트랜잭션 확인')
            self.factory_status.setText('복원이 아직 끝나지 않았습니다. 미완료 트랜잭션을 계속 처리하세요. 현재 상태가 복원 성공을 의미하지는 않습니다.')

    def backup_recovery_controls(self, layout):
        form = self.box(layout, '복원 원본')
        self.field(form, 'restore_backup', '백업 폴더', kind='folder')
        choose_zip = W.QPushButton('또는 전체 백업 ZIP 선택…')
        choose_zip.clicked.connect(lambda: self.browse(self.fields['restore_backup'], 'file', 'Backup (*.zip);;All files (*)'))
        form.addRow('', choose_zip)
        self.field(form, 'restore_work', '복원 작업 폴더', kind='folder', value=self.work('recovery'))
        self.button(layout, '선택한 백업 검사(오프라인)', 'backup_inspect', lambda: {'backup': self.text('restore_backup')})
        auto = W.QPushButton('원클릭 되돌리기: 최초 전체 백업을 찾아서 사용')
        auto.setObjectName('one_click_restore')
        auto.clicked.connect(self.one_click_restore)
        self.buttons.append(auto)
        layout.addWidget(auto)
        self.button(layout, '선택한 백업 복원 및 전체 리드백', 'restore', lambda: dict(self.common(),
            backup=self.text('restore_backup'), workspace=self.text('restore_work')), primary=True)
        self.recovery_accept = self.reload_controls(layout, '복원 후 화면 표시, 촬영 및 재생 정상')
        self.button(layout, '이전 백업 복원 후 로드 상태 확인', 'restore_verify', lambda: {
            'workspace': self.text('restore_work'), 'accepted': self.recovery_accept.isChecked()})
        note = W.QLabel('카메라가 여전히 서비스 통신에 진입할 수 있어야 합니다. 이 기능은 카메라 본체 백업을 사용하며, 공식 BODYDATA.DAT를 백업으로 플래시하지 않습니다.')
        note.setWordWrap(True)
        layout.addWidget(note)

    def fl23_page(self):
        layout = self.page('FL2 / FL3', '백업을 만들 필요 없이 바로 활성화／제거합니다. 제거는 FL2／FL3의 목록 항목만 끄며, 커스텀 룩에 필요한 공용 색상 코드는 유지합니다. 나중에 다시 활성화할 수 있습니다.')
        self.button(layout, 'FL2 / FL3 활성화／다시 활성화', 'direct_fl23_enable',
                    lambda: self.direct_args('fl23', mode='fl23_enable'), primary=True)
        self.button(layout, '목록에서 FL2 / FL3 제거, 커스텀 룩은 유지', 'direct_fl23_disable',
                    lambda: self.direct_args('fl23', mode='fl23_disable'))
        info = W.QLabel('쓰기와 리드백 후 팝업에 따라 전원을 완전히 끄고 다시 켠 뒤, 카메라에서 확인하면 바로 사용할 수 있습니다. 다시 컴퓨터에 연결해 확인할 필요는 없습니다.')
        info.setWordWrap(True); layout.addWidget(info)
        diagnostic = self.advanced(layout, '로드 확인(선택 진단, 사용 전제 조건 아님)')
        self.fl23_accept = self.reload_controls(diagnostic, '메뉴, 촬영 및 재생 정상')
        self.button(diagnostic, '로드된 버전을 읽기 전용으로 확인(선택)', 'direct_verify',
                    lambda: self.direct_verify_args('fl23', self.fl23_accept.isChecked()))
        layout.addStretch()

    def look_page(self):
        from .lookeditor import LookEditor
        layout=self.page('커스텀 크리에이티브 룩','이번에 바꿀 슬롯만 필요한 만큼 추가하세요. 추가하지 않은 슬롯은 그대로 유지됩니다. 한 번의 작업으로 룩 쓰기와 ST 기본값 복원을 함께 할 수 있습니다.')
        self.look_pending=W.QLabel();self.look_pending.setWordWrap(True);layout.addWidget(self.look_pending)
        self.look_editor=LookEditor();layout.addWidget(self.look_editor)
        self.look_button=W.QPushButton('작업 항목을 추가한 뒤 계속');self.look_button.setProperty('primary',True)
        self.look_button.setObjectName('action_direct_looks_import');self.look_button.clicked.connect(self.launch_look_changes)
        layout.addWidget(self.look_button);self.buttons.append(self.look_button)
        tools=W.QHBoxLayout();layout.addLayout(tools)
        for title,callback in [('내장 DCP／A7Look 짝 라이브러리 열기',self.open_profile_library),('DCP／LUT로 A7Look 생성…',self.compiler_dialog),('LrC 자동 인식 플러그인 설치…',self.plugin_dialog)]:
            button=W.QPushButton(title);button.clicked.connect(callback);tools.addWidget(button);self.buttons.append(button)
        formats=W.QPushButton('LUT 형식 안내: ST / sRGB이며 S-Log3가 아님…')
        formats.clicked.connect(self.show_look_format_guide);layout.addWidget(formats);self.buttons.append(formats)
        layout.addWidget(W.QLabel('쓰기 페이지에서는 .a7look을 선택합니다. .cube / .dcp는 먼저 위의 도구로 변환하세요. 목록에 있는 슬롯만 변경합니다.'))
        hint=W.QLabel('한 번의 작업으로 현재 상태 확인, 쓰기, 전체 리드백을 자동으로 합니다. 끝나면 팝업에 따라 전원을 완전히 끄고 다시 켜서 로드하며, 별도로 확인 버튼을 누를 필요가 없습니다.')
        hint.setWordWrap(True);layout.addWidget(hint)
        layout.addStretch()
        self._look_displayed_pending=None;self._look_refreshing=False
        self.look_editor.changed.connect(self.refresh_look_controls)
        self.refresh_look_controls()

    def open_profile_library(self):
        from .profilelibrary import location
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(location())))

    def look_pending_state(self):
        prior=self.settings.get('look_direct_work')
        if not prior:return None
        try:
            job=self.settings.get('look_job_dir')
            if job and Path(job,'status.private.json').is_file():
                status=json.loads(Path(job,'status.private.json').read_text(encoding='utf-8'))
                request=json.loads(Path(job,'request.private.json').read_text(encoding='utf-8'))
                matches=prior in (request.get('arguments',{}).get('workspace'),status.get('result',{}).get('workspace'))
                if matches and status.get('phase')=='SUCCEEDED':
                    prior=status['result']['workspace']
                elif matches and status.get('phase') in ('RUNNING','QUEUED') and self.process is None:
                    return prior,dict(phase='BACKGROUND_UNCONFIRMED',error='백그라운드 작업이 끝났는지 아직 확인되지 않았습니다. 원래 결과를 기다리거나 로그를 확인하세요. 중복 쓰기는 하지 않습니다.')
            if not Path(prior,'direct.private.json').is_file():
                prior=self.settings.get('look_resume_source')
                if not prior or not Path(prior,'direct.private.json').is_file():return None
            state=json.loads(Path(prior,'direct.private.json').read_text(encoding='utf-8'))
            nested=Path(prior,'program-install/direct.private.json')
            if state.get('phase')=='RESOURCE_RELOAD_VERIFIED' and nested.is_file():
                prior=str(nested.parent);state=json.loads(nested.read_text(encoding='utf-8'))
        except (ValueError,OSError,KeyError) as exc:return str(prior),dict(phase='UNREADABLE',error=str(exc))
        return None if state.get('phase') in ('AWAITING_RELOAD','LOADED','COMPLETE') else (prior,state)

    def refresh_look_controls(self):
        if self._look_refreshing:return
        self._look_refreshing=True
        try:
            pending=self.look_pending_state();busy=self.process is not None
            if pending:
                prior,state=pending;phase=state.get('phase')
                token=(prior,phase)
                if self._look_displayed_pending!=token:
                    options=state.get('bundle_options',{});by_slot={r['slot']:r for r in options.get('looks',[])}
                    operations=options.get('slot_operations') or [dict(r,action='write') for r in by_slot.values()]+[dict(slot=n,action='restore') for n in options.get('reset_slots',[])]
                    if not operations and state.get('mode')=='slot_default':operations=[dict(slot=state['slot'],action='restore')]
                    if not operations and state.get('mode')=='look_import':operations=[dict(slot=state['slot'],action='write',path=str(Path(prior)/'reference.a7look'),name=state.get('name') or '')]
                    operations=[dict(r,**{k:v for k,v in by_slot.get(r['slot'],{}).items() if k!='slot'}) if r.get('action')=='write' else r for r in operations]
                    self.look_editor.set_operations(operations);self._look_displayed_pending=token
                self.look_editor.setEnabled(False);self.look_button.setEnabled(not busy)
                if phase in ('AWAITING_RESOURCE_RELOAD','RESOURCE_RELOAD_VERIFIED') or (phase=='NEEDS_REVIEW' and state.get('forward_name_build',{}).get('stream_install') and 'readonly=-16' in state.get('error','')):
                    text='지난 작업이 리소스 이어 가기를 기다리고 있으며, 저장된 목록을 그대로 사용합니다.'
                    self.look_button.setText('슬롯 변경 계속')
                else:
                    from .lookbatch import read_only_failure
                    if read_only_failure(state):
                        text='지난번에는 쓰기 전에 중지되었고 이전 기록은 보존되었습니다. 목록을 조정한 뒤 다시 시도할 수 있습니다.'
                        self.look_editor.setEnabled(not busy)
                        self.look_button.setText('이번 변경 다시 시도(지난번에는 쓰지 않음)')
                        self.look_button.setEnabled(not busy and bool(self.look_editor.values()) and not self.look_editor.errors())
                    else:
                        text='지난 작업이 아직 끝나지 않았습니다. 원래 작업 기록을 확인하세요. 자동으로 다시 쓰지 않습니다.'
                        self.look_button.setText('미완료 작업 기록 보기')
                self.look_pending.setText(text);self.look_pending.show()
            else:
                if self._look_displayed_pending is not None:
                    self._look_displayed_pending=None;self.look_editor.set_operations([])
                self.look_pending.hide();self.look_editor.setEnabled(not busy)
                rows=self.look_editor.values()
                self.look_button.setText('이 %d개 변경 사항 확인 후 적용'%len(rows) if rows else '작업 항목을 추가한 뒤 계속')
                self.look_button.setEnabled(bool(rows) and not self.look_editor.errors() and not busy)
        finally:self._look_refreshing=False

    def look_batch_args(self):
        return self.direct_args('look',operations=self.look_editor.values())

    def launch_look_changes(self):
        pending=self.look_pending_state()
        if pending:
            prior,state=pending;phase=state.get('phase')
            if phase in ('AWAITING_RESOURCE_RELOAD','RESOURCE_RELOAD_VERIFIED') or (phase=='NEEDS_REVIEW' and state.get('forward_name_build',{}).get('stream_install') and 'readonly=-16' in state.get('error','')):
                self.launch('direct_resume_looks',dict(self.common(),workspace=self.work('look'),failed_workspace=prior));return
            from .lookbatch import read_only_failure
            if read_only_failure(state):
                self.launch('direct_looks_import',self.look_batch_args());return
            W.QMessageBox.warning(self,'지난 작업이 아직 끝나지 않았습니다',state.get('error','원래 기록을 보존하고, 알 수 없는 결과를 확인한 뒤 계속하세요.')+'\n작업 폴더: '+prior)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(prior));return
        self.launch('direct_looks_import',self.look_batch_args())

    def vertical_page(self):
        layout = self.page('세로 표시', '카메라를 세로로 들면 촬영 정보, 아이콘과 레이아웃이 방향에 맞춰 회전합니다. 켜면 네이티브 스위치가 저장되어 전원을 완전히 끄고 다시 켠 뒤 자동으로 적용됩니다. MENU 전체가 회전하는 것은 아닙니다.')
        form = self.box(layout, '표시 작업')
        self.field(form, 'vertical_work', '작업 폴더', kind='folder', value=str(data_root() / 'vertical'))
        args = lambda: dict(self.common(), workspace=self.text('vertical_work'))
        self.button(layout, '세로 표시 켜기', 'vertical_enable', args, primary=True)
        self.button(layout, '세로 표시 끄기', 'vertical_disable', args)
        frame_hint=W.QLabel('켜기는 세로 표시 스위치를 저장하고 현재의 Fn／하위 메뉴 표시 보정을 적용합니다. 끄기는 먼저 세로 표시를 끈 뒤 표시 보정을 되돌립니다. 두 작업 모두 반복해서 덮어쓸 수 있습니다.')
        frame_hint.setWordWrap(True);layout.addWidget(frame_hint)
        known=W.QLabel('알려진 표시 문제: 세로 크리에이티브 룩 선택 화면의 테두리가 여전히 두껍고, 눈금은 가로로 표시되지만 주황색 포인터 방향은 아직 수정되지 않았습니다.')
        known.setWordWrap(True);layout.addWidget(known)
        hint = W.QLabel('켜기／끄기는 선택한 쓰기와 리드백을 한 번에 마친 뒤, 전원을 완전히 끄고 다시 켜서 로드하라는 팝업을 한 번 띄웁니다. 같은 상태에서 반복해서 눌러도 같은 내용은 다시 쓰지 않습니다.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()

    def sound_page(self):
        layout = self.page('전자 셔터음', '별도의 스위치로 전자 셔터 소리 분기를 제어합니다. 오디오를 변환한 뒤 바로 저장할 수 있으며, 임시 미리 듣기는 선택입니다. 쓰기 후 팝업에 따라 전원을 완전히 끄고 다시 켜서 로드합니다.')
        form = self.box(layout, '사운드 작업 공간')
        self.field(form, 'sound_work', '작업 폴더', kind='folder', value=str(data_root() / 'sound'))
        self.field(form, 'sound_backup', '두 번째 백업', kind='folder', value=str(data_root() / 'sound-backup'))
        args = lambda: dict(self.common(), workspace=self.text('sound_work'), backup=self.text('sound_backup'))
        row = W.QHBoxLayout()
        layout.addLayout(row)
        self.button(row, '전자 셔터음 켜기', 'sound_on', args)
        self.button(row, '전자 셔터음 끄기', 'sound_off', args)
        media = self.box(layout, '오디오／영상 구간 가져오기')
        self.field(media, 'sound_source', '사운드 파일', kind='file', filter='Audio / video (*.wav *.mp3 *.mp4 *.m4a *.flac *.ogg);;All files (*)')
        self.sound_start = W.QDoubleSpinBox()
        self.sound_start.setRange(0, 86400)
        self.sound_start.setDecimals(3)
        media.addRow('시작점(초)', self.sound_start)
        self.sound_duration = W.QDoubleSpinBox()
        self.sound_duration.setRange(.01, 2)
        self.sound_duration.setDecimals(3)
        self.sound_duration.setValue(.5)
        media.addRow('길이(초)', self.sound_duration)
        row = W.QHBoxLayout()
        layout.addLayout(row)
        self.button(row, '선택 구간 변환', 'sound_import', lambda: dict(args(), source=self.text('sound_source'),
            start=self.sound_start.value(), duration=self.sound_duration.value(), peak=.4))
        self.button(row, '카메라에서 임시 미리 듣기', 'sound_audition', args)
        volume = W.QHBoxLayout()
        layout.addLayout(volume)
        self.button(volume, '음량 낮추기 −3 dB', 'sound_volume', lambda: dict(args(), decibels=-3))
        self.button(volume, '음량 높이기 +3 dB', 'sound_volume', lambda: dict(args(), decibels=3))
        self.button(volume, '변환 당시 음량으로 되돌리기', 'sound_volume', lambda: dict(args(), reset=True))
        volume_hint=W.QLabel('먼저 구간을 변환한 뒤 음량을 조절하세요. 컴퓨터의 오디오만 처리하며 카메라에 연결하지 않습니다. 음량 상한에 도달하면 클리핑을 막기 위해 자동으로 제한하며, 저장 시에는 조정한 소리를 사용합니다. 다시 변환하면 음량이 초기화됩니다.')
        volume_hint.setWordWrap(True);layout.addWidget(volume_hint)
        self.button(layout, '현재 커스텀 사운드 저장', 'sound_install', args, primary=True)
        hint=W.QLabel('변환에 성공하면 바로 저장할 수 있습니다. 작업 공간을 자동으로 만들고 전자 셔터음 지원을 준비하므로, 먼저 켜거나 이전 작업과 연결할 필요가 없습니다. 미리 듣기는 선택이며, 모든 쓰기가 끝나면 팝업에 따라 한 번에 전원을 완전히 끄고 다시 켜서 로드합니다.')
        hint.setWordWrap(True);layout.addWidget(hint)
        optional=self.advanced(layout,'미리 듣기 확인 및 복원 기록(선택, 저장에 영향 없음)')
        self.button(optional, '미리 듣기 정상 확인', 'sound_confirm', lambda: dict(args(), result='correct'))
        self.button(optional, '전원 차단으로 원래 소리 복원됨, 복원 완료 기록', 'sound_confirm_recovery', args)
        self.sound_accept = self.reload_controls(layout, '소리 정상, 촬영 및 재생 정상')
        self.button(layout, '재시작 후 사운드 확인', 'sound_verify', lambda: dict(args(), accepted=self.sound_accept.isChecked()))
        self.button(layout, '순정 효과음으로 복원', 'sound_restore_sound', args)
        layout.addStretch()

    def show_zadig_guide(self):
        from .zadigguide import TITLE, TEXT, URL
        dialog = W.QDialog(self); dialog.setWindowTitle(TITLE); dialog.resize(820, 680)
        layout = W.QVBoxLayout(dialog)
        content = W.QTextBrowser(); content.setPlainText(TEXT); layout.addWidget(content)
        row = W.QHBoxLayout(); layout.addLayout(row)
        download = W.QPushButton('Zadig 다운로드 페이지 열기'); download.setProperty('primary', True)
        download.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(URL)))
        close = W.QPushButton('닫기'); close.clicked.connect(dialog.accept)
        row.addWidget(download); row.addWidget(close)
        dialog.exec()

    def show_look_format_guide(self):
        from .lookguide import TITLE,TEXT
        dialog=W.QDialog(self);dialog.setWindowTitle(TITLE);dialog.resize(800,650)
        layout=W.QVBoxLayout(dialog)
        content=W.QTextBrowser();content.setPlainText(TEXT);layout.addWidget(content)
        close=W.QPushButton('닫기');close.clicked.connect(dialog.accept);layout.addWidget(close)
        dialog.exec()

    def compiler_dialog(self):
        dialog = W.QDialog(self)
        dialog.setWindowTitle('A7Look 생성')
        dialog.resize(760, 370)
        layout = W.QVBoxLayout(dialog)
        hint = W.QLabel('CUBE: 입력은 반드시 Sony ST / sRGB, 출력은 sRGB여야 합니다. S-Log3 / S-Gamut3.Cine이나 기타 Log LUT는 바로 사용할 수 없습니다.\nDCP: 오프라인 해석과 피팅이 내장되어 있어 Lightroom, 플러그인, 네트워크가 필요 없습니다. LookTable／톤 커브를 변환하며, 카메라 내 결과는 피팅 근사치입니다. 이 도구는 .a7look만 생성하고 카메라에 쓰지 않습니다.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        help_button=W.QPushButton('어떤 형식과 색 공간을 지원하나요? 전체 안내 보기…')
        help_button.clicked.connect(self.show_look_format_guide);layout.addWidget(help_button)
        form = self.box(layout, '원본 파일 및 출력')
        self.field(form, 'compile_source', 'DCP / CUBE', kind='file', filter='Reference (*.dcp *.cube)')
        self.field(form, 'compile_name', '룩 이름')
        self.field(form, 'compile_output', '출력 A7Look', kind='save', filter='A7Look (*.a7look)')
        self.field(form, 'compile_model', '응답 파일', kind='file', filter='Response (*.npz)',
                   value=str(ROOT / 'calibration/a7c2-2.01.virtual.npz'))
        run = W.QPushButton('생성 시작')
        run.clicked.connect(lambda: (dialog.accept(), self.launch('compile_look', {
            'source': self.text('compile_source'), 'name': self.text('compile_name'),
            'output': self.text('compile_output'), 'model': self.text('compile_model'),
            'input_space': 'sony-st-srgb', 'passes': 3})))
        layout.addWidget(run)
        dialog.exec()

    def plugin_dialog(self):
        self.launch('plugin_install', {})

    def one_click_restore(self):
        roots = [str(data_root() / 'backups'), str(data_root() / 'initial-checkpoints')]
        for key in ('fl23_backup', 'look_backup', 'restore_backup'):
            if key in self.fields and self.text(key):
                roots.append(self.text(key))
        self.fields['restore_work'].setText(self.work('recovery'))
        self.launch('restore_auto', dict(self.common(), roots=roots, workspace=self.text('restore_work')))

    def launch(self, action, arguments):
        if self.process is not None:
            return
        factory_finished = False
        if action == 'setup_all' and not self.drivers_first(arguments):
            return
        if action=='direct_looks_import' and arguments.get('failed_workspace'):
            action='direct_resume_looks'
        if action=='direct_looks_import':
            try:
                from .worker import setup_runtime
                setup_runtime()
                from .lookbatch import options
                chosen=options(arguments.get('operations',arguments.get('looks',[])))
                arguments=dict(arguments,operations=chosen['slot_operations'])
            except (ValueError,OSError,KeyError,TypeError) as exc:
                W.QMessageBox.warning(self,'작업 목록을 확인하세요',str(exc));return
        if action == 'sound_audition':
            from .actions.sound import has_conversion
            try:
                ready = has_conversion(arguments['workspace'])
            except (ValueError, OSError, KeyError) as exc:
                W.QMessageBox.warning(self, '사운드 작업 공간을 확인해야 합니다', str(exc));return
            if not ready:
                W.QMessageBox.information(self, '먼저 구간을 변환하세요', '아직 변환된 사운드가 없습니다. 먼저 사운드 파일을 선택하고 시작점과 길이를 설정한 뒤 「선택 구간 변환」을 누르고, 성공하면 미리 들어 보세요.');return
        if action == 'restore_verify' and not Path(arguments.get('workspace', ''), 'rollback.private.json').is_file():
            # This button checks a backup restore only. A built-in or firmware
            # file restore is checked by the factory verification button.
            answer = W.QMessageBox.question(self, '여기에는 이전 백업 복원 기록이 없습니다',
                '"이전 백업 복원 후 로드 상태 확인"은 "이전 백업으로 과거 상태 되돌리기"에만 사용합니다.\n'
                '내장 공장 기준이나 펌웨어 파일로 복원한 뒤에는 "재시작 후 확인하고 복원 완료"를 눌러야 합니다.\n지금 공장 복원 확인으로 바꿀까요?',
                W.QMessageBox.Yes | W.QMessageBox.Cancel, W.QMessageBox.Yes)
            if answer != W.QMessageBox.Yes:
                return
            action, arguments = 'direct_verify', self.direct_verify_args('factory', bool(arguments.get('accepted')))
        if action == 'direct_factory_reset':
            # A failed fresh attempt must not hide the original interrupted
            # transaction. Resolve the explicitly selected evidence locally,
            # before the power/write confirmation and before any USB access.
            from .worker import setup_runtime
            setup_runtime()
            from looktool.factoryrecover import pending, FINISHED
            try:
                found = pending(data_root() / 'work', self.text('factory_failed_work'))
            except (ValueError, OSError):
                found = None  # Records are evidence only; the worker re-plans.
            if found:
                # A finished restore is verified first; the worker re-plans
                # from current bytes if the camera changed since then.
                original, prior = found
                factory_finished = prior['phase'] in FINISHED
                action = 'direct_resume_factory'
                arguments = dict(arguments, failed_workspace=str(original), accepted=self.factory_accept.isChecked())
        if action == 'driver_uninstall':
            answer = W.QMessageBox.question(self, '카메라 드라이버 제거',
                'Sony 054C:0E8A／0336의 서드파티 드라이버를 백업한 뒤 제거하여 Windows가 카메라를 다시 인식하게 합니다.\n'
                '완료 후 USB를 뽑았다 다시 꽂고 대용량 저장장치 모드를 다시 선택해야 할 수 있습니다. 이 작업은 카메라 펌웨어를 수정하지 않습니다.\n지금 제거할까요?',
                W.QMessageBox.Yes | W.QMessageBox.Cancel, W.QMessageBox.Cancel)
            if answer != W.QMessageBox.Yes:
                return
        writing = action in jobs.POWER_ACTIONS
        if writing:
            target = (('홈에서 체크한 기능; 커스텀 1–6 교체: ' + presets.SUMMARY if arguments.get('options', {}).get('preset_pack') else '홈에서 체크한 기능과 선택한 A7Look 슬롯') if action == 'setup_all' else
                      '연결된 카메라 본체의 가장 오래된 사용 가능한 전체 백업을 자동 선택' if action == 'restore_auto' else
                      arguments.get('backup') if action == 'restore' else arguments.get('look') or arguments.get('workspace'))
            if action in jobs.DIRECT_ACTIONS:
                target = ('; '.join('커스텀 %s → %s' % (r['slot'], 'ST 기본값으로 복원' if r['action']=='restore' else '쓰기 '+r['name']) for r in arguments.get('operations', []))
                          if action == 'direct_looks_import' else
                          '이름이 이미 기록된 룩 설치 계속: '+arguments.get('failed_workspace','') if action=='direct_resume_looks' else
                          ('지난 복원 확인. 카메라 내용이 바뀌었다면 현재 바이트 기준으로 다시 복원: ' if factory_finished else
                           '이름／설정 복원 마저 완료: ') + arguments.get('failed_workspace', '') if action == 'direct_resume_factory' else
                          '세로 표시＋Fn／하위 메뉴 프레임 선 보정' if action=='vertical_enable' else
                          '세로 표시 끄기 및 프레임 선 보정 되돌리기' if action=='vertical_disable' else
                          '공장 2.01 기능' if action == 'direct_factory_reset' else
                          'FL2／FL3: 활성화' if action == 'direct_fl23_enable' else
                          'FL2／FL3: 목록에서 제거' if action == 'direct_fl23_disable' else
                          '커스텀 %s: %s' % (arguments.get('slot'), 'ST 기본값' if action == 'direct_slot_default'
                                           else arguments.get('name') or Path(arguments.get('look', '')).stem))
            scope = ('목록의 쓰기와 복원을 한 번에 적용하며, 추가하지 않은 슬롯은 그대로 유지됩니다. FL23, 사운드, 세로 표시는 유지됩니다. 쓰기와 리드백이 끝나면 전원을 완전히 끄고 다시 켜라는 안내를 한 번에 표시합니다.\n' if action=='direct_looks_import' else
                     '드라이버를 자동으로 준비하고 홈에서 체크한 기능을 설치합니다. 전원을 완전히 끄고 다시 켜는 것은 안내에 따라 직접 해야 합니다. 결과가 불분명하면 중지하며, 자동으로 다시 플래시하지 않습니다.\n' if action == 'setup_all' else
                     '먼저 읽기 전용으로 확인합니다. 카메라 내용이 바뀐 경우에만 현재 바이트 기준으로 쓰며, 이미 공장 상태와 같은 내용은 다시 쓰지 않습니다.\n' if action == 'direct_resume_factory' and factory_finished else
                     '현재 바이트를 기준으로 미완료 단계를 이어 가며, 이미 복원된 내용은 다시 쓰지 않습니다. 원래 실패한 트랜잭션은 보존되며, 결과가 불분명하면 먼저 보고서를 읽어 확인합니다.\n' if action == 'direct_resume_factory' else
                     '프로젝트의 프로그램 수정을 제거하고 커스텀 슬롯 6개를 ST 기본값으로 설정합니다. 이전 레시피는 되찾지 않습니다. 먼저 카메라에서 ST를 선택하세요.\n' if action == 'direct_factory_reset' else
                     'FL2／FL3 목록 항목만 끄며, LUT와 공용 코드는 유지합니다. 먼저 카메라에서 ST를 선택하세요.\n' if action == 'direct_fl23_disable' else
                     '선택한 커스텀 슬롯을 ST 기본값으로 설정하고 다른 슬롯은 유지합니다.\n' if action == 'direct_slot_default' else '')
            answer = W.QMessageBox.question(self, '카메라 쓰기 시작',
                scope + '대상: ' + str(target) + '\n카메라가 ILCE-7CM2, 펌웨어 2.01이고 배터리가 충분한지(최소 50% 권장) 확인하세요.\n'
                '다른 프로젝트에서 수정한 펌웨어는 이 도구와 충돌할 수 있습니다.\n'
                '작업과 전체 리드백 동안 전원과 USB 연결을 유지하고, 전원을 끄거나 케이블을 뽑지 마세요.\n전원이 충분한지 확인했으며 시작할까요?',
                W.QMessageBox.Yes | W.QMessageBox.Cancel, W.QMessageBox.Cancel)
            if answer != W.QMessageBox.Yes:
                return
            arguments = dict(arguments, power_confirmed=True)
            arguments.pop('battery', None)
        if action in jobs.DIRECT_ACTIONS:
            group = 'factory' if action in ('direct_factory_reset', 'direct_resume_factory') else 'fl23' if 'fl23' in action else 'look'
            self.settings[group + '_direct_work'] = arguments['workspace']
            if group=='look':
                self.settings['look_resume_source']=arguments.get('failed_workspace','')
            if group!='look':getattr(self, group + '_accept').setChecked(False)
        if action == 'setup_all':
            self.settings['setup_work'] = arguments['workspace']
            self.setup_button.setText('설치 중…')
            for box in (self.setup_presets,self.setup_fl23,self.setup_sound,self.setup_vertical,self.setup_plugin):box.setEnabled(False)
        for key, field in self.fields.items():
            self.settings[key] = field.text()
        write_json(data_root() / 'desktop.json', self.settings)
        self.current_job = jobs.new_job(action, arguments)
        if action in ('direct_looks_import','direct_resume_looks') or (action=='direct_verify' and arguments.get('workspace')==self.settings.get('look_direct_work')):
            self.settings['look_job_dir']=str(self.current_job)
            write_json(data_root()/'desktop.json',self.settings)
        self.log_offset = 0
        self.log.clear()
        self.camera_operation = action in jobs.CAMERA_ACTIONS or action == 'driver_install'
        for button in self.buttons:
            button.setEnabled(False)
        self.look_editor.setEnabled(False)
        self.stop.setEnabled(not self.camera_operation)
        self.progress.setRange(0, 0)
        self.status.setText('실행 중: ' + action + '. 상세 로그는 계속 저장됩니다.')
        self.process = QtCore.QProcess(self)
        self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        env = QtCore.QProcessEnvironment.systemEnvironment()
        env.insert('PYTHONUTF8', '1')
        env.insert('PYTHONIOENCODING', 'utf-8')
        self.process.setProcessEnvironment(env)
        command = jobs.worker_command(self.current_job / 'request.private.json')
        self.process.readyReadStandardOutput.connect(self.process_output)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)
        self.process.start(command[0], command[1:])

    def process_output(self):
        text = bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace')
        if text:
            with (self.current_job / 'process.log').open('a', encoding='utf-8') as stream:
                stream.write(text)
            self.log.appendPlainText(text.rstrip())

    def tail_log(self):
        if not self.current_job:
            return
        path = self.current_job / 'worker.log'
        if path.exists():
            with path.open('rb') as stream:
                stream.seek(self.log_offset)
                text = stream.read()
                self.log_offset = stream.tell()
            if text:
                self.log.appendPlainText(text.decode('utf-8', errors='replace').rstrip())

    def poll_job(self):
        self.tail_log()
        if self.process is None and (self._look_displayed_pending is not None or self.look_pending_state()):self.refresh_look_controls()
        # The status file also recovers completion if Qt missed its signal.
        if self.process is not None and self.process.state() == QtCore.QProcess.NotRunning:
            self.finished(self.process.exitCode(), self.process.exitStatus())

    def finished(self, code, exit_status):
        if self.process is None:
            return
        self.tail_log()
        status_path = self.current_job / 'status.private.json'
        status = json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else {}
        success = code == 0 and status.get('phase') == 'SUCCEEDED'
        if success and (status.get('action') in ('usb_prepare', 'connect') or
                        (status.get('action') == 'setup_all' and 'drivers' in status.get('result', {}).get('completed_steps', []))):
            self.settings['connection_verified'] = True
        if success and status.get('action') == 'restore_auto':
            self.fields['restore_backup'].setText(status['result']['backup'])
            self.fields['restore_work'].setText(status['result']['workspace'])
        if success and status.get('action') == 'look_restore' and 'look_work' in self.fields:
            self.fields['look_work'].setText(status['result']['workspace'])
        if success and status.get('action') in ('direct_factory_reset', 'direct_resume_factory'):
            self.settings['factory_direct_work'] = status['result']['workspace']
            self.fields['factory_failed_work'].setText(status['result']['workspace'])
        if success and status.get('action')=='direct_verify' and status['result'].get('mode')=='factory_reset':
            self.settings['factory_direct_work']=status['result']['workspace']
        if success and (status.get('action') in ('direct_looks_import','direct_resume_looks') or
                (status.get('action')=='direct_verify' and status['result'].get('mode') in ('install_all','look_import','slot_default'))):
            self.settings['look_direct_work']=status['result']['workspace']
            self.settings.pop('look_resume_source',None)
        if success and status.get('action') == 'setup_all':
            for key in ('sound_workspace','sound_backup'):
                target='sound_work' if key=='sound_workspace' else 'sound_backup'
                if status['result'].get(key):self.fields[target].setText(status['result'][key])
        for key, field in self.fields.items():
            self.settings[key] = field.text()
        write_json(data_root() / 'desktop.json', self.settings)
        waiting=success and reloadguide.required(status.get('action'),status.get('result',{}))
        sound_notice=reloadguide.sound_notice(status.get('action'),status.get('result',{})) if success else None
        factory_wait=success and status.get('result',{}).get('mode')=='factory_reset' and status['result'].get('phase')=='AWAITING_RELOAD'
        factory_done=success and status.get('result',{}).get('mode')=='factory_reset' and status['result'].get('phase') in ('LOADED','COMPLETE')
        if factory_done:self.clear_installation_state()
        elif factory_wait:self.clear_installation_state(verified=False)
        resource_wait=status.get('result',{}).get('phase') in ('WAIT_RESOURCE_RELOAD','AWAITING_RESOURCE_RELOAD')
        resource_ready=success and status.get('result',{}).get('phase')=='RESOURCE_RELOAD_VERIFIED'
        setup_wait=waiting and status.get('action')=='setup_all'
        self.status.setText(('이름은 기록되었고 프로그램은 아직 쓰지 않았습니다. 팝업에 따라 전원을 완전히 끄고 다시 켠 뒤 USB를 다시 연결하고 홈의 "설치 계속"을 누르세요.' if resource_wait and setup_wait else
                             '이름은 기록되었고 프로그램은 아직 쓰지 않았습니다. 팝업에 따라 전원을 완전히 끄고 다시 켠 뒤 USB를 다시 연결하고 커스텀 룩 페이지의 "슬롯 변경 계속"을 누르세요.' if resource_wait else
                             '룩 일괄 쓰기가 끝났고 전체 리드백도 완료했습니다. 팝업에 따라 한 번에 전원을 완전히 끄고 다시 켜면 로드됩니다.' if status.get('action')=='direct_looks_import' else
                             'FL23 작업 완료. 쓰기와 전체 리드백이 모두 끝났습니다. 카메라를 다시 켜면 적용되며, 확인 버튼을 누를 필요는 없습니다.' if
                             status.get('result',{}).get('mode') in ('fl23_enable','fl23_disable') else
                             '읽기/쓰기가 끝났습니다. USB 분리 → 램프가 꺼질 때까지 대기 → 전원 끄고 램프 소등 대기 → 배터리 최소 1분 분리 → 전원 켜기 → USB 다시 연결 → 홈의 "설치 계속" 클릭. 자세한 내용은 팝업을 보세요.' if setup_wait else
                             '읽기/쓰기가 끝났습니다. USB 분리 → 램프가 꺼질 때까지 대기 → 전원 끄고 램프 소등 대기 → 배터리 최소 1분 분리 → 전원 켜기. 자세한 내용은 팝업을 보세요.') if waiting else
                           '이름과 읽기 전용 마운트를 확인했습니다. 프로그램은 아직 설치되지 않았으니 홈의 "설치 계속"을 누르세요.' if resource_ready and status.get('action')=='setup_all' else
                           '이름과 읽기 전용 마운트를 확인했습니다. 프로그램은 아직 설치되지 않았으니 커스텀 룩 페이지의 "슬롯 변경 계속"을 누르세요.' if resource_ready else
                           '완료. 로그 안내에 따라 다음 단계를 진행하세요.' if success else
                           '작업 중지: ' + status.get('error', '프로세스가 비정상 종료되었습니다. 종료 코드 ' + str(code)) + '. 진단 패키지를 내보내세요.')
        if not success and sys.platform == 'win32' and self.driver_step_failed(status.get('action')):
            self.status.setText(self.status.text() + DRIVER_HINT)
        if sound_notice:self.status.setText(sound_notice['status'])
        if factory_wait:self.status.setText('복원이 아직 끝나지 않았습니다: 쓰기와 리드백은 끝났으며, 반드시 전원을 차단해 재시작하고 USB를 다시 연결한 뒤 "재시작 후 확인하고 복원 완료"를 눌러야 합니다.')
        elif factory_done:self.status.setText('공장 복원 확인을 통과했고 이전 설치 상태를 지웠습니다. 카메라의 촬영과 재생을 계속 확인하세요. 다시 설치하려면 홈의 "원클릭 설치"를 누르세요.')
        self.progress.setRange(0, 100)
        self.progress.setValue(70 if factory_wait else 0 if sound_notice and status.get('action')=='sound_install' else
                              40 if success and (resource_wait or resource_ready) else 70 if waiting and status.get('action')=='setup_all' else 100 if success else 0)
        for button in self.buttons:
            button.setEnabled(True)
        self.stop.setEnabled(False)
        self.process.deleteLater()
        self.process = None
        if success and status.get('action') in ('direct_looks_import','direct_resume_looks','direct_verify') and status.get('result',{}).get('mode') in ('install_all','look_import','slot_default') and status['result'].get('phase') in ('COMPLETED','AWAITING_RELOAD','LOADED','COMPLETE') and (status['action']!='direct_verify' or self._look_displayed_pending is not None):
            self.look_editor.set_operations([])
        self.refresh_look_controls()
        self.refresh_setup_controls()
        self.refresh_factory_controls()
        if not success and status.get('action')=='direct_factory_reset':
            from looktool.factoryrecover import prewrite_temporary_conflict
            work=self.settings.get('factory_direct_work')
            path=Path(work,'direct.private.json') if work else None
            try:state=json.loads(path.read_text(encoding='utf-8')) if path and path.is_file() else {}
            except (OSError,ValueError):state={}
            if prewrite_temporary_conflict(state) and state.get('error')==status.get('error'):
                self.status.setText('이번 복원은 아직 쓰지 않았습니다. 팝업에 따라 전원을 완전히 차단하고 USB를 다시 연결한 뒤 "전원 차단 후 복원 재시도"를 누르세요.')
                self.show_reload_guide(mode='factory_prewrite_temporary')
        if sound_notice:
            W.QMessageBox.information(self,sound_notice['title'],sound_notice['text'])
        elif status.get('action')=='usb_stability':
            if success:
                result=status['result']
                W.QMessageBox.information(self,'USB 읽기 전용 검사 통과',
                    '%d회, 총 %.1f MB의 프로그램 파일 읽기 전용 전송을 %.0f초 동안 마쳤으며, 회차별 바이트가 일치했습니다.\n\n펌웨어나 설정에 쓰거나 네이티브 쓰기 프로그램을 실행하지 않았습니다.\n\n%s'%
                    (len(result['rounds']),result['total_bytes']/1024/1024,result.get('seconds',0),result['note']))
            else:
                error=status.get('error','로그를 확인하고 케이블을 바꾼 뒤 다시 검사하세요.')
                W.QMessageBox.warning(self,'USB 연결 검사가 완료되지 않았습니다' if '완료되지 않았' in error else 'USB 연결 검사를 통과하지 못했습니다',error)
        elif waiting:
            self.show_reload_guide(setup=status.get('action')=='setup_all', mode=('resource_reload' if resource_wait else status.get('action') if
                status.get('action','').startswith('sound_') else status.get('result',{}).get('mode')))
        elif success and status.get('action')=='setup_all' and status.get('result',{}).get('phase')=='COMPLETE':
            self.status.setText('설치가 완료되었습니다. 지난 설치 결과를 볼 수 있으며, 다시 플래시할 필요는 없습니다.')
            self.setup_button.setText('새 원클릭 설치 시작')
            self.setup_result_button.setVisible(True)
            for box in (self.setup_presets,self.setup_fl23,self.setup_sound,self.setup_vertical,self.setup_plugin):box.setEnabled(True)
            self.show_setup_result()

    def process_error(self, error):
        if self.process and error == QtCore.QProcess.FailedToStart:
            self.log.appendPlainText(self.process.errorString())
            self.finished(-1, None)

    def open_job(self):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.current_job or data_root())))

    def export_logs(self):
        if not self.current_job:
            return
        path = W.QFileDialog.getSaveFileName(self, '진단 패키지 내보내기', 'a7c2-diagnostics.zip', 'ZIP (*.zip)')[0]
        if path:
            try:
                jobs.export_support(self.current_job, path)
            except Exception as exc:
                W.QMessageBox.warning(self, '내보내기 실패', str(exc))

    def cancel_offline(self):
        if self.process and not self.camera_operation:
            self.process.terminate()

    def closeEvent(self, event):
        if self.process:
            W.QMessageBox.information(self, '작업이 아직 실행 중입니다', '현재 작업이 끝난 뒤 닫으세요. 카메라에 쓰는 동안 프로그램을 종료하거나 케이블을 뽑지 마세요.')
            event.ignore()
        else:
            event.accept()


def main():
    app = W.QApplication.instance() or W.QApplication(sys.argv)
    if '--smoke-test' in sys.argv and sys.platform == 'win32':
        # Qt's headless test platform does not enumerate Windows system fonts.
        import os
        for name in ('malgun.ttf', 'msyh.ttc'):
            font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name
            if font.is_file():
                QtGui.QFontDatabase.addApplicationFont(str(font))
    app.setApplicationName('A7C2Tools')
    app.setOrganizationName('QAQLeonard')
    window = Window()
    window.show()
    if '--smoke-test' in sys.argv:
        data_root().mkdir(parents=True, exist_ok=True)
        for index in range(window.pages.count()):
            window.nav.setCurrentRow(index)
            app.processEvents()
            if not window.grab().save(str(data_root() / ('desktop-page-' + str(index + 1) + '.png'))):
                raise OSError('Could not save desktop smoke screenshot')
        window.nav.setCurrentRow(0)
        QtCore.QTimer.singleShot(800, app.quit)
    else:
        QtCore.QTimer.singleShot(0,window.show_connection_notice)
    return app.exec()
