"""Color tokens and Qt style sheets for the desktop GUI.

Only colors, borders and states live here. Padding, font sizes and widths keep
their previous values so widget sizes and the page layout stay unchanged.
"""
from string import Template
from .paths import ROOT

COLORS = dict(
    window='#ffffff', sidebar='#f3f6fb', surface='#ffffff', raised='#f4f7fb', group='#f9fbfe',
    hover='#e8f0fc', border='#dce3ed', strong='#c4cfdd', strong_hover='#a3b2c6',
    text='#1f2633', muted='#5d6a7e', faint='#a3aebd', nav_text='#3a4558',
    log='#f7f9fc', log_text='#2f3a4b',
    accent='#1f6feb', accent_hover='#3b82f6', accent_pressed='#1a5fd0', accent_text='#ffffff',
    accent_soft='#e8f1fe', accent_line='#a9c7f5', accent_disabled_text='#8aa6d6', selection='#cfe1fd',
    warning='#a15c00', danger='#c62828', danger_soft='#fdecec',
)
ICONS = ROOT / 'src/desktopapp/assets'


def _sheet(text):
    return Template(text).substitute(COLORS, check=(ICONS / 'check.svg').as_posix(),
                                     chevron=(ICONS / 'chevron-down.svg').as_posix(),
                                     chevron_up=(ICONS / 'chevron-up.svg').as_posix())


_COMMON = '''
QLabel[hint="true"],QLabel[muted="true"] {color:$muted;}
QLabel[warning="true"] {color:$warning;}
QComboBox::drop-down {border:0;width:24px;}
QComboBox::down-arrow {image:url("$chevron");width:10px;height:10px;}
QComboBox QAbstractItemView {background:$raised;border:1px solid $strong;color:$text;
    selection-background-color:$accent_soft;selection-color:$accent;outline:0;}
QPushButton:hover {background:$hover;border-color:$strong;}
QPushButton:pressed {background:$border;}
QPushButton:disabled {background:$surface;color:$faint;border-color:$border;}
QPushButton[primary="true"] {background:$accent;color:$accent_text;border-color:$accent;font-weight:600;}
QPushButton[primary="true"]:hover {background:$accent_hover;border-color:$accent_hover;}
QPushButton[primary="true"]:pressed {background:$accent_pressed;}
QPushButton[primary="true"]:disabled {background:$accent_soft;color:$accent_disabled_text;border-color:$accent_line;}
QLineEdit:focus,QComboBox:focus,QSpinBox:focus,QDoubleSpinBox:focus,QPlainTextEdit:focus {border-color:$accent_line;}
QLineEdit:hover,QComboBox:hover,QSpinBox:hover,QDoubleSpinBox:hover {border-color:$strong;}
QLineEdit:disabled,QComboBox:disabled {color:$faint;}
QScrollArea {border:0;}
QScrollBar:vertical {background:transparent;width:8px;margin:0;}
QScrollBar::handle:vertical {background:$strong;border-radius:4px;min-height:30px;}
QScrollBar::handle:vertical:hover {background:$strong_hover;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {background:transparent;}
QAbstractSpinBox::up-button,QAbstractSpinBox::down-button {border:0;background:transparent;width:16px;}
QAbstractSpinBox::up-button:hover,QAbstractSpinBox::down-button:hover {background:$hover;}
QAbstractSpinBox::up-arrow {image:url("$chevron_up");width:10px;height:10px;}
QAbstractSpinBox::down-arrow {image:url("$chevron");width:10px;height:10px;}
QToolTip {background:$raised;color:$text;border:1px solid $strong;padding:6px;}
'''

WINDOW = _sheet('''
QWidget {font-family:"Segoe UI","Malgun Gothic","Apple SD Gothic Neo","PingFang SC","Microsoft YaHei";font-size:13px;color:$text;background:$window;}
QLineEdit,QSpinBox,QDoubleSpinBox,QComboBox,QPlainTextEdit {background:$surface;border:1px solid $border;border-radius:6px;
    padding:7px;selection-background-color:$selection;selection-color:$text;}
QPlainTextEdit {background:$log;color:$log_text;font-family:"Cascadia Mono","Consolas","Menlo","Malgun Gothic","Apple SD Gothic Neo","Microsoft YaHei";}
QPushButton {background:$raised;border:1px solid $border;border-radius:6px;padding:8px 13px;color:$text;}
QListWidget {background:$sidebar;border:0;border-right:1px solid $border;padding:10px;outline:0;}
QListWidget::item {padding:16px 12px;border-radius:7px;color:$nav_text;}
QListWidget::item:hover {background:$surface;color:$text;}
QListWidget::item:selected {background:$accent_soft;color:$accent;border-left:3px solid $accent;padding-left:9px;}
QGroupBox {border:1px solid $border;border-radius:8px;margin-top:15px;padding:16px 11px 10px;background:$group;}
QGroupBox::title {subcontrol-origin:margin;left:12px;padding:0 5px;color:$muted;background:$window;}
QGroupBox QLabel,QGroupBox QCheckBox {background:transparent;}
QProgressBar {border:0;background:transparent;height:5px;}
QProgressBar::chunk {background:$accent;border-radius:2px;margin:8px 0;}
QCheckBox {spacing:8px;padding:5px 0;background:transparent;}
QCheckBox::indicator {width:16px;height:16px;border:1px solid $strong;border-radius:4px;background:$surface;}
QCheckBox::indicator:hover {border-color:$accent_line;}
QCheckBox::indicator:checked {background:$accent;border-color:$accent;image:url("$check");}
QCheckBox::indicator:disabled {background:$raised;border-color:$border;}
QCheckBox::indicator:checked:disabled {background:$accent_line;border-color:$accent_line;}
QCheckBox:disabled {color:$faint;}
QToolButton {background:transparent;border:0;color:$muted;padding:3px 2px;}
QToolButton:hover,QToolButton:checked {color:$accent;}
QMessageBox,QDialog {background:$window;}
QTextBrowser {background:$surface;border:1px solid $border;border-radius:6px;}
''' + _COMMON)

EDITOR = _sheet('''
QWidget {font-family:"Segoe UI","Malgun Gothic","Apple SD Gothic Neo","Microsoft YaHei";font-size:13px;color:$text;background:$window;}
QLabel[title="true"] {font-size:27px;font-weight:650;color:$text;}
QLabel[badge="true"] {background:$accent_soft;color:$accent;border-radius:5px;padding:6px 10px;}
QFrame[card="true"] {background:$group;border:1px solid $border;border-radius:9px;}
QFrame[card="true"] QLabel {background:transparent;}
QComboBox,QLineEdit {background:$window;border:1px solid $strong;border-radius:5px;padding:8px;min-height:19px;
    selection-background-color:$selection;}
QComboBox {padding-right:25px;}
QPushButton {background:$raised;border:1px solid $border;border-radius:6px;padding:9px 15px;color:$text;}
QWidget#operationDetails,QStackedWidget#operationStack {background:transparent;}
''' + _COMMON + '''
QComboBox::down-arrow {image:none;}
QPushButton[quiet="true"] {background:transparent;border:0;color:$muted;padding:6px;}
QPushButton[quiet="true"]:hover {background:$danger_soft;color:$danger;}
''')
