"""Incremental custom-slot editor. This module never opens a camera."""
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets as W
from .theme import COLORS, EDITOR as STYLE


def label(text, *, muted=False):
    result=W.QLabel(text)
    result.setProperty('muted',muted)
    return result


class Choice(W.QComboBox):
    def paintEvent(self,event):
        super().paintEvent(event)
        painter=QtGui.QPainter(self);painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen);painter.setBrush(QtGui.QColor(COLORS['muted']))
        x,y=self.width()-13,self.height()//2
        painter.drawPolygon(QtGui.QPolygon([QtCore.QPoint(x-4,y-2),QtCore.QPoint(x+4,y-2),QtCore.QPoint(x,y+3)]))


class Operation(W.QFrame):
    changed=QtCore.Signal()
    remove_requested=QtCore.Signal(object)

    def __init__(self,slot):
        super().__init__()
        self.setProperty('card',True)
        self.file_path=''
        box=W.QVBoxLayout(self);box.setContentsMargins(17,15,17,15);box.setSpacing(12)
        top=W.QHBoxLayout();box.addLayout(top)
        top.addWidget(label('대상 슬롯',muted=True))
        self.slot=Choice()
        for number in range(1,7):self.slot.addItem('커스텀 '+str(number),number)
        self.slot.setCurrentIndex(slot-1);self.slot.setFixedWidth(115)
        top.addWidget(self.slot);top.addSpacing(18)
        top.addWidget(label('이번 작업',muted=True))
        self.action=Choice();self.action.addItems(['ST 기본값으로 복원','A7Look 쓰기'])
        self.action.setFixedWidth(160);top.addWidget(self.action);top.addStretch()
        remove=W.QPushButton('이 항목 제거');remove.setProperty('quiet',True)
        remove.setToolTip('이 작업 항목만 제거하며, 해당 슬롯은 그대로 유지됩니다')
        remove.clicked.connect(lambda:self.remove_requested.emit(self));top.addWidget(remove)
        self.details=W.QStackedWidget();self.details.setObjectName('operationStack');box.addWidget(self.details)
        restore=W.QWidget();restore.setObjectName('operationDetails');restore_box=W.QHBoxLayout(restore);restore_box.setContentsMargins(0,0,0,0)
        note=label('이 슬롯을 ST 기본값으로 복원합니다. 가져오기 전의 이전 레시피는 되찾지 않습니다.',muted=True)
        restore_box.addWidget(note);restore_box.addStretch();self.details.addWidget(restore)
        write=W.QWidget();write.setObjectName('operationDetails');grid=W.QGridLayout(write);grid.setContentsMargins(0,0,0,0);grid.setHorizontalSpacing(12);grid.setVerticalSpacing(6)
        grid.addWidget(label('A7Look 파일',muted=True),0,0)
        grid.addWidget(label('카메라 표시 이름',muted=True),0,2)
        self.file=W.QLineEdit();self.file.setReadOnly(True);self.file.setPlaceholderText('.a7look 파일을 선택하세요')
        self.browse=W.QPushButton('파일 선택…');self.browse.clicked.connect(self.choose_file)
        self.name=W.QLineEdit();self.name.setPlaceholderText('비워 두면 파일 내 이름 사용')
        grid.addWidget(self.file,1,0);grid.addWidget(self.browse,1,1);grid.addWidget(self.name,1,2)
        grid.setColumnStretch(0,3);grid.setColumnStretch(2,2);self.details.addWidget(write)
        self.action.currentIndexChanged.connect(self.update_mode)
        self.slot.currentIndexChanged.connect(self.changed)
        self.name.textChanged.connect(self.changed)
        self.update_mode()

    def update_mode(self):
        self.details.setCurrentIndex(self.action.currentIndex())
        self.details.setFixedHeight(36 if self.action.currentIndex()==0 else 74)
        self.setFixedHeight(115 if self.action.currentIndex()==0 else 153)
        self.changed.emit()

    def choose_file(self):
        from .profilelibrary import location
        path=W.QFileDialog.getOpenFileName(self,'A7Look 파일 선택',self.file_path or str(location()),'A7Look (*.a7look)')[0]
        if path:self.set_file(path)

    def set_file(self,path):
        self.file_path=str(path);self.file.setText(Path(path).name);self.file.setToolTip(str(path));self.changed.emit()

    def value(self):
        value=dict(slot=self.slot.currentData(),action='restore' if self.action.currentIndex()==0 else 'write')
        if value['action']=='write':value.update(path=self.file_path,name=self.name.text().strip())
        return value


class LookEditor(W.QWidget):
    changed=QtCore.Signal()

    def __init__(self):
        super().__init__();self.rows=[];self._loading=False;self.setStyleSheet(STYLE)
        box=W.QVBoxLayout(self);box.setContentsMargins(0,0,0,0);box.setSpacing(12)
        top=W.QHBoxLayout();self.add=W.QPushButton('+ 작업 항목 추가')
        self.add.clicked.connect(lambda:self.add_row());top.addWidget(self.add)
        top.addWidget(label('새 항목의 기본값은 "ST 기본값으로 복원"이며, 쓰기로 바꿀 수 있습니다.',muted=True));top.addStretch();box.addLayout(top)
        self.row_box=W.QVBoxLayout();self.row_box.setSpacing(12);box.addLayout(self.row_box)
        self.empty=W.QFrame();self.empty.setProperty('card',True)
        empty_box=W.QVBoxLayout(self.empty);empty_box.setContentsMargins(20,25,20,25)
        empty_title=label('아직 추가한 작업 항목이 없습니다');empty_title.setAlignment(QtCore.Qt.AlignCenter)
        empty_title.setStyleSheet('font-size:17px;font-weight:600;');empty_box.addWidget(empty_title)
        note=label('"작업 항목 추가"를 눌러 대상 슬롯과 작업을 선택하세요. 현재 6개 슬롯은 모두 그대로 유지됩니다.',muted=True)
        note.setWordWrap(True);note.setAlignment(QtCore.Qt.AlignCenter);empty_box.addWidget(note)
        self.row_box.addWidget(self.empty)
        summary=W.QFrame();summary.setProperty('card',True)
        summary_box=W.QVBoxLayout(summary);summary_box.setContentsMargins(15,12,15,12)
        self.summary=label('');self.summary.setStyleSheet('font-size:15px;font-weight:600;');summary_box.addWidget(self.summary)
        self.preserved=label('',muted=True);self.preserved.setWordWrap(True);summary_box.addWidget(self.preserved)
        self.validation=label('');self.validation.setProperty('warning',True);self.validation.setWordWrap(True);summary_box.addWidget(self.validation)
        box.addWidget(summary);self.update_summary()

    def add_row(self,slot=None):
        occupied={r.slot.currentData() for r in self.rows};free=[n for n in range(1,7) if n not in occupied]
        if not free:return None
        row=Operation(slot if slot is not None else free[0]);self.rows.append(row)
        row.changed.connect(self.update_summary);row.remove_requested.connect(self.remove_row)
        self.row_box.addWidget(row);self.update_summary();return row

    def remove_row(self,row):
        self.rows.remove(row);self.row_box.removeWidget(row);row.setParent(None);row.deleteLater();self.update_summary()

    def values(self):return [row.value() for row in self.rows]

    def set_operations(self,operations):
        self._loading=True
        try:
            for row in list(self.rows):self.remove_row(row)
            for value in operations:
                row=self.add_row(value['slot'])
                if value.get('action','write')=='write':
                    row.action.setCurrentIndex(1);row.set_file(value.get('path') or value.get('file',''))
                    row.name.setText(value.get('name') or '')
        finally:self._loading=False
        self.update_summary()

    def errors(self):
        values=self.values();slots=[r['slot'] for r in values];errors=[]
        if len(slots)!=len(set(slots)):errors.append('같은 슬롯에는 작업을 하나만 추가할 수 있습니다.')
        missing=[str(r['slot']) for r in values if r['action']=='write' and not r['path']]
        if missing:errors.append('커스텀 '+', '.join(missing)+'에 사용할 A7Look 파일을 선택하세요.')
        return errors

    def update_summary(self):
        values=self.values();used={r['slot'] for r in values};writes=sum(r['action']=='write' for r in values)
        self.empty.setVisible(not values);self.add.setEnabled(len(values)<6)
        for row in self.rows:
            for i in range(6):row.slot.model().item(i).setEnabled(i+1 not in used or i+1==row.slot.currentData())
        self.summary.setText('이번에 %d개 슬롯 변경: 쓰기 %d개 · 복원 %d개'%(len(values),writes,len(values)-writes))
        kept=[str(n) for n in range(1,7) if n not in used]
        self.preserved.setText('그대로 유지: '+('커스텀 '+', '.join(kept) if kept else '없음(6개 슬롯 모두 이번 목록에 있음)'))
        self.validation.setText(' '.join(self.errors()));self.validation.setVisible(bool(self.errors()))
        if not self._loading:self.changed.emit()
