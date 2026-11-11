#!/usr/bin/env python3
"""Workflow ETA desktop companion. No ComfyUI imports or generation controls."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

try:
    from PySide6.QtCore import Qt,QTimer,QSettings,QUrl,Signal,QObject,QLockFile,QStandardPaths,QEvent
    from PySide6.QtGui import QFont,QIcon,QDesktopServices,QGuiApplication,QFontMetrics
    from PySide6.QtNetwork import QNetworkAccessManager,QNetworkRequest,QNetworkReply
    from PySide6.QtWidgets import (QApplication,QWidget,QLabel,QVBoxLayout,QHBoxLayout,QGridLayout,QToolButton,
        QProgressBar,QSizeGrip,QDialog,QFormLayout,QLineEdit,QComboBox,QCheckBox,QDoubleSpinBox,QSpinBox,
        QSlider,QDialogButtonBox,QMessageBox,QPushButton,QFrame,QSizePolicy)
except ImportError:
    raise SystemExit('PySide6 is required. On CachyOS/Arch: sudo pacman -S pyside6\nThen run this app with the same Python used by install.py.')
from panel_model import Snapshot,server_url,MAX_REPLY

VERSION='0.1.0'
THEMES={
 'ocean':('Ocean','#15232e','#243b4c','#f1f7fa','#8ca5b5','#67d5bf','#ffba70','#ff8895'),
 'midnight':('Midnight','#101426','#202744','#f3f5ff','#9caacb','#8ba8ff','#f5be7b','#ff8f9f'),
 'graphite':('Graphite','#202226','#383b40','#ffffff','#a3a9b3','#b4d3e5','#f7bd77','#ff929a'),
 'forest':('Forest','#132a21','#234338','#f3fff5','#98bba8','#88d99a','#eec477','#ff9294'),
 'ember':('Ember','#2b201b','#50352a','#fff7f0','#c0a391','#ffa867','#ffd17f','#ff9696'),
 'amethyst':('Amethyst','#251a31','#423052','#fcf5ff','#b8a0cb','#d2a0ff','#f2c37c','#ff95ba'),
 'paper':('Paper','#f4f7f9','#d0dce3','#122936','#536b7c','#087b69','#915500','#b42640')}

class StatusPoller(QObject):
    received=Signal(dict)
    failed=Signal(str)
    def __init__(self,parent=None):
        super().__init__(parent);self.manager=QNetworkAccessManager(self)
        self.timer=QTimer(self);self.timer.timeout.connect(self.poll)
        self.deadline=QTimer(self);self.deadline.setSingleShot(True);self.deadline.timeout.connect(self.timeout)
        self.reply=None;self.generation=0;self.url='';self.reason=''
    def configure(self,url,seconds):
        self.generation+=1;self.timer.stop();self.deadline.stop()
        old=self.reply;self.reply=None
        if old is not None:old.abort()
        self.url=server_url(url);self.timer.setInterval(int(seconds*1000));self.timer.start();self.poll()
    def timeout(self):
        if self.reply is not None:self.reason='ComfyUI did not respond within 4 seconds.';self.reply.abort()
    def poll(self):
        if self.reply is not None or not self.url:return
        generation=self.generation;self.reason='';body=bytearray()
        request=QNetworkRequest(QUrl(self.url+'/workflow/eta/status'))
        request.setRawHeader(b'Accept',b'application/json')
        request.setRawHeader(b'Cache-Control',b'no-cache')
        request.setAttribute(QNetworkRequest.Attribute.CacheLoadControlAttribute,QNetworkRequest.CacheLoadControl.AlwaysNetwork)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        reply=self.manager.get(request);self.reply=reply;self.deadline.start(4000)
        def read():
            if generation!=self.generation:return
            if not reply.isOpen():return
            data=bytes(reply.readAll())
            if len(body)+len(data)>MAX_REPLY:
                self.reason='Status response exceeds 8 MiB. Check the server URL or reduce oversized workflow metadata.';reply.abort()
            else:body.extend(data)
        def finished():
            if generation!=self.generation:reply.deleteLater();return
            read();self.deadline.stop();self.reply=None
            try:
                code=reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
                if self.reason:raise ValueError(self.reason)
                if code==404:raise ValueError('Workflow ETA endpoint not found. Install/enable Workflow ETA and restart ComfyUI.')
                if code is not None and code!=200:raise ValueError(f'ComfyUI returned HTTP {code}. Check the URL, access rules or login requirements.')
                if reply.error()!=QNetworkReply.NetworkError.NoError:raise ValueError(reply.errorString())
                try:payload=json.loads(body)
                except (ValueError,UnicodeError):raise ValueError('Server returned non-JSON data. Check the ComfyUI base URL.') from None
                from panel_model import valid_payload
                self.received.emit(valid_payload(payload))
            except ValueError as error:self.failed.emit(str(error))
            finally:reply.deleteLater()
        reply.readyRead.connect(read);reply.finished.connect(finished)
    def stop(self):
        self.generation+=1;self.timer.stop();self.deadline.stop()
        reply=self.reply;self.reply=None
        if reply is not None:reply.abort()

class MoveLabel(QLabel):
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            window=self.window().windowHandle()
            if window is not None and window.startSystemMove():event.accept();return
        super().mousePressEvent(event)

def label(text='',name=None):
    widget=QLabel(text);widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:widget.setObjectName(name)
    return widget

def tool(text,tip,callback,checkable=False):
    button=QToolButton();button.setText(text);button.setToolTip(tip);button.setCheckable(checkable);button.clicked.connect(callback)
    return button

class KWinKeepAbove(QObject):
    """Apply the preference through KWin on native Wayland, without blocking Qt."""
    failed=Signal(str)
    def __init__(self,parent):
        super().__init__(parent)
        from PySide6.QtDBus import QDBusConnection
        import tempfile
        self.bus=QDBusConnection.sessionBus();self.directory=tempfile.TemporaryDirectory(prefix='workflow-eta-kwin-')
        self.name='workflow-eta-panel-'+str(os.getpid());self.busy=False;self.desired=None;self.closed=False
    def call(self,path,interface,method,args,callback):
        from PySide6.QtDBus import QDBusMessage,QDBusPendingCallWatcher
        message=QDBusMessage.createMethodCall('org.kde.KWin',path,interface,method);message.setArguments(args)
        watcher=QDBusPendingCallWatcher(self.bus.asyncCall(message,1500),self)
        def finished(_):
            reply=watcher.reply();watcher.deleteLater()
            if not self.closed:callback(reply)
        watcher.finished.connect(finished)
    def request(self,enabled):
        self.desired=bool(enabled)
        if self.busy or self.closed:return
        self.busy=True;self.active=self.desired
        self.call('/Scripting','org.kde.kwin.Scripting','unloadScript',[self.name],self.load)
    def load(self,reply):
        from PySide6.QtDBus import QDBusMessage
        if reply.type()==QDBusMessage.MessageType.ErrorMessage:self.finish(reply.errorMessage());return
        path=Path(self.directory.name)/'keep-above.js'
        # Scope to this process and the main panel; do not affect settings or other apps.
        script="""function apply(w) {
            if (w.pid === PID && w.caption === 'Workflow ETA Panel') w.keepAbove = ENABLED;
        }
        workspace.windowList().forEach(apply);
        workspace.windowAdded.connect(apply);
        """.replace('PID',str(os.getpid())).replace('ENABLED','true' if self.active else 'false')
        try:path.write_text(script,encoding='utf-8')
        except OSError as error:self.finish(str(error));return
        self.call('/Scripting','org.kde.kwin.Scripting','loadScript',[str(path),self.name],self.run)
    def run(self,reply):
        from PySide6.QtDBus import QDBusMessage
        if reply.type()==QDBusMessage.MessageType.ErrorMessage:self.finish(reply.errorMessage());return
        args=reply.arguments()
        if not args or not isinstance(args[0],int) or args[0]<0:self.finish('KWin could not load the panel window script.');return
        self.call('/Scripting/Script'+str(args[0]),'org.kde.kwin.Script','run',[],self.ran)
    def ran(self,reply):
        from PySide6.QtDBus import QDBusMessage
        self.finish(reply.errorMessage() if reply.type()==QDBusMessage.MessageType.ErrorMessage else '')
    def finish(self,error):
        self.busy=False
        if error:self.failed.emit(error)
        if self.desired!=self.active:self.request(self.desired)
    def close(self):
        if self.closed:return
        from PySide6.QtDBus import QDBusMessage
        self.closed=True
        message=QDBusMessage.createMethodCall('org.kde.KWin','/Scripting','org.kde.kwin.Scripting','unloadScript');message.setArguments([self.name]);self.bus.asyncCall(message,1500)
        self.directory.cleanup()

class SettingsDialog(QDialog):
    def __init__(self,panel):
        super().__init__(panel);self.setWindowTitle('Workflow ETA Panel settings');self.setMinimumWidth(380)
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        self.url=QLineEdit(panel.url);self.url.setPlaceholderText('http://127.0.0.1:8188');form.addRow('ComfyUI base URL',self.url)
        self.interval=QSpinBox();self.interval.setRange(1,10);self.interval.setSuffix(' seconds');self.interval.setValue(panel.interval);form.addRow('Refresh every',self.interval)
        self.theme=QComboBox()
        for key,data in THEMES.items():self.theme.addItem(data[0],key)
        self.theme.setCurrentIndex(self.theme.findData(panel.theme));form.addRow('Theme',self.theme)
        scale_row=QWidget();scale_layout=QHBoxLayout(scale_row);scale_layout.setContentsMargins(0,0,0,0)
        self.scale=QSlider(Qt.Orientation.Horizontal);self.scale.setRange(25,200);self.scale.setSingleStep(5);self.scale.setPageStep(25)
        self.scale.setAccessibleName('Scale UI');self.scale.setToolTip('Scale the panel from 25% to 200%. Applied when you save.')
        self.scale.setValue(round(panel.scale*100))
        self.scale_value=QSpinBox();self.scale_value.setRange(25,200);self.scale_value.setSingleStep(5);self.scale_value.setSuffix('%');self.scale_value.setValue(self.scale.value())
        self.scale_value.setAccessibleName('Scale UI percentage')
        self.scale.valueChanged.connect(self.scale_value.setValue);self.scale_value.valueChanged.connect(self.scale.setValue)
        scale_layout.addWidget(self.scale,1);scale_layout.addWidget(self.scale_value);form.addRow('Scale UI',scale_row)
        self.opacity=QSlider(Qt.Orientation.Horizontal);self.opacity.setRange(40,100);self.opacity.setValue(round(panel.windowOpacity()*100));form.addRow('Opacity (40–100%)',self.opacity)
        self.top=QCheckBox('Keep panel above other windows');self.top.setChecked(panel.top);form.addRow(self.top)
        self.border=QCheckBox('Show native window title bar');self.border.setChecked(panel.border);form.addRow(self.border)
        note=label('On Wayland, KDE manages window placement. Drag the title area to move the panel; use KDE window rules for a fixed position or all desktops.')
        note.setWordWrap(True);note.setMaximumWidth(460);layout.addWidget(note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def validate(self):
        try:server_url(self.url.text())
        except ValueError as error:QMessageBox.warning(self,'Invalid server URL',str(error));return
        self.accept()

class Panel(QWidget):
    def __init__(self,settings=None,start_polling=True):
        super().__init__();self.setObjectName('panel');self.setWindowTitle('Workflow ETA Panel')
        self.settings=settings if settings is not None else QSettings()
        self.snapshot=Snapshot();self._fit_pending=False;self._height_key=None
        self.kwin=None
        if QGuiApplication.platformName().lower().startswith('wayland') and 'kde' in os.environ.get('XDG_CURRENT_DESKTOP','').lower():
            self.kwin=KWinKeepAbove(self);self.kwin.failed.connect(self.keep_above_failed)
            QApplication.instance().aboutToQuit.connect(self.kwin.close)
        try:self.url=server_url(str(self.settings.value('server','http://127.0.0.1:8188')))
        except ValueError:self.url='http://127.0.0.1:8188'
        def number_setting(key,default,minimum,maximum):
            try:return max(minimum,min(maximum,float(self.settings.value(key,default))))
            except (ValueError,TypeError):return default
        def flag(key,default):return str(self.settings.value(key,str(default))).lower() in ('true','1')
        self.theme=str(self.settings.value('theme','ocean'));self.theme=self.theme if self.theme in THEMES else 'ocean'
        self.scale=number_setting('scale',1.,.25,2.);self.interval=int(number_setting('interval',1,1,10))
        self.compact=flag('compact',False);self.top=flag('top',True);self.border=flag('border',True)
        self.setWindowOpacity(number_setting('opacity',1.,.4,1.))
        root=QVBoxLayout(self);root.setContentsMargins(18,14,18,10);root.setSpacing(10)
        header=QHBoxLayout();self.brand=MoveLabel('WORKFLOW ETA');self.brand.setObjectName('brand');self.brand.setToolTip('Drag here to move the panel')
        header.addWidget(self.brand);header.addStretch()
        self.pin=tool('↑','Keep above other windows',self.toggle_top,True);self.pin.setChecked(self.top)
        self.collapse=tool('▸' if self.compact else '▾','Switch compact / expanded view',self.toggle_compact)
        self.gear=tool('⚙','Settings',self.open_settings)
        header.addWidget(self.pin);header.addWidget(self.collapse);header.addWidget(self.gear);root.addLayout(header)
        self.state=label('● Connecting','status');root.addWidget(self.state)
        self.hero=QFrame();self.hero.setObjectName('hero');hero=QVBoxLayout(self.hero);hero.setContentsMargins(16,12,16,12);hero.setSpacing(4)
        self.caption=label('ESTIMATED REMAINING','caption');self.big=label('—','countdown');self.badge=label('','badge')
        self.big.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        hero.addWidget(self.caption);hero.addWidget(self.big);hero.addWidget(self.badge);root.addWidget(self.hero)
        self.progress=QProgressBar();self.progress.setRange(0,1000);self.progress.setTextVisible(False);self.progress.setFixedHeight(5);self.progress.setToolTip('Estimated workflow progress from elapsed and remaining time; not sampler step completion.');root.addWidget(self.progress)
        self.quick=label('Elapsed —  ·  Finish —');self.quick.setWordWrap(True);root.addWidget(self.quick)
        self.details=QWidget();detail_layout=QVBoxLayout(self.details);detail_layout.setContentsMargins(0,0,0,0);detail_layout.setSpacing(10)
        metrics=QGridLayout();metrics.setHorizontalSpacing(18);self.total_caption=label('Expected total','muted');self.finish_caption=label('Expected finish','muted')
        self.total=label('—','metric');self.finish=label('—','metric');self.finish.setWordWrap(True)
        metrics.addWidget(self.total_caption,0,0);metrics.addWidget(self.finish_caption,0,1);metrics.addWidget(self.total,1,0);metrics.addWidget(self.finish,1,1);metrics.setColumnStretch(0,1);metrics.setColumnStretch(1,1)
        detail_layout.addLayout(metrics)
        self.started=label('Started —','muted');self.started.setWordWrap(True);detail_layout.addWidget(self.started)
        self.current=label('Waiting for node');self.current.setWordWrap(True);self.current.setMaximumHeight(60);detail_layout.addWidget(self.current)
        self.counts=label('','muted');detail_layout.addWidget(self.counts)
        self.message=label('Connecting…','muted');self.message.setWordWrap(True);self.message.setMaximumHeight(90);detail_layout.addWidget(self.message)
        root.addWidget(self.details)
        footer=QHBoxLayout();self.open_button=QPushButton('Open ComfyUI');self.open_button.clicked.connect(lambda:QDesktopServices.openUrl(QUrl(self.url)))
        footer.addWidget(self.open_button);footer.addStretch();self.sync=label('','muted');footer.addWidget(self.sync);footer.addWidget(QSizeGrip(self));root.addLayout(footer)
        self._scaled_layouts=[(lay,lay.getContentsMargins(),lay.spacing()) for lay in (root,header,hero,detail_layout,metrics,footer)]
        self.poller=StatusPoller(self);self.poller.received.connect(self.accept);self.poller.failed.connect(self.fail)
        self.animation=QTimer(self);self.animation.setInterval(250);self.animation.timeout.connect(self.render);self.animation.start()
        self.apply_style();self.apply_flags();self.details.setVisible(not self.compact)
        self.resize(int(number_setting('width',390*self.scale,75,2400)),int(number_setting('height',460*self.scale,45,2400)))
        geometry=self.settings.value('geometry')
        if geometry is not None and not QGuiApplication.platformName().lower().startswith('wayland'):
            self.restoreGeometry(geometry)
        self.render();self.schedule_fit()
        if start_polling:self.poller.configure(self.url,self.interval)
    def showEvent(self,event):
        super().showEvent(event)
        if self.kwin:QTimer.singleShot(0,lambda:self.kwin.request(self.top))
    def keep_above_failed(self,error):
        message='KDE could not apply Keep above: '+error+'\nUse Alt+F3 → More Actions → Keep Above Others, or a KDE window rule.'
        self.pin.setToolTip(message);print(message,file=sys.stderr)
        if self.isVisible() and not getattr(self,'_keep_above_warned',False):
            self._keep_above_warned=True;QMessageBox.warning(self,'Keep above unavailable',message)
    def apply_flags(self):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint,self.top)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint,not self.border)
    def apply_style(self):
        _,bg,card,text,muted,accent,warning,danger=THEMES[self.theme]
        px=lambda value:max(1,round(value*self.scale))
        for lay,margins,spacing in self._scaled_layouts:
            lay.setContentsMargins(*(round(v*self.scale) for v in margins));lay.setSpacing(px(spacing))
            if isinstance(lay,QGridLayout):lay.setHorizontalSpacing(px(18))
        self.progress.setFixedHeight(px(5));self.current.setMaximumHeight(px(60));self.message.setMaximumHeight(px(90))
        self.setStyleSheet(f'''
            QWidget#panel, QDialog {{background:{bg};color:{text};}}
            QLabel {{color:{text};background:transparent;}}
            QLabel#muted,QLabel#caption,QLabel#badge {{color:{muted};}}
            QFrame#hero {{background:{card};border-radius:{px(12)}px;}}
            QToolButton,QPushButton {{background:{card};color:{text};border:1px solid {muted};border-radius:{px(6)}px;padding:{px(5)}px {px(8)}px;}}
            QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox {{background:{card};color:{text};border:1px solid {muted};padding:{px(5)}px;}}
            QCheckBox {{color:{text};}}
            QToolButton:checked {{color:{accent};border-color:{accent};}}
            QProgressBar {{border:0;background:{card};border-radius:2px;}}
            QProgressBar::chunk {{background:{accent};border-radius:2px;}}
        ''')
        font=QFont();font.setPointSizeF(10*self.scale);self.setFont(font)
        for child in self.findChildren(QLabel):child.setFont(font)
        for button in (self.pin,self.collapse,self.gear,self.open_button):button.setFont(font)
        for widget,points in ((self.brand,9),(self.caption,10),(self.total,17),(self.finish,12),(self.quick,11)):
            f=QFont(font);f.setPointSizeF(points*self.scale);f.setBold(True);widget.setFont(f)
        # Bound wrapped status text so long node names and diagnostics cannot inflate the window.
        line=QFontMetrics(font).lineSpacing()
        for widget,lines in ((self.quick,2),(self.started,2),(self.finish,2),(self.current,2),(self.message,3)):
            widget.setMaximumHeight(lines*QFontMetrics(widget.font()).lineSpacing()+2)
            widget.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Maximum)
        self.setMinimumWidth(round(300*self.scale))
        self.render()
    def schedule_fit(self):
        if self._fit_pending:return
        self._fit_pending=True;QTimer.singleShot(0,self.fit_content)
    def fit_content(self):
        self._fit_pending=False
        self.layout().invalidate();self.layout().activate()
        height=self.layout().totalHeightForWidth(self.width())
        if height<0:height=self.sizeHint().height()
        self.resize(self.width(),max(self.minimumSizeHint().height(),height))
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if event.oldSize().width()!=event.size().width():self.schedule_fit()
    def render(self):
        view=self.snapshot.view(time.monotonic(),self.interval);self.last_view=view
        _,bg,card,text,muted,accent,warning,danger=THEMES[self.theme]
        tone=accent if view['online'] and view['state']!='aborted' else warning if view['stale'] or view['status']=='Connecting' else danger
        completed='#806000' if self.theme=='paper' else '#ffe066'
        self.state.setText('● '+view['status']);self.state.setStyleSheet('color:'+tone);self.state.setToolTip(view['message'])
        self.caption.setText(view['title'].upper());self.big.setText(view['value'])
        self.big.setStyleSheet('color:'+(completed if view['online'] and view['state']=='done' else text))
        font=QFont('DejaVu Sans Mono');font.setBold(True);font.setPointSizeF(38*self.scale)
        available=max(140,self.width()-76)
        while QFontMetrics(font).horizontalAdvance(view['value'])>available and font.pointSizeF()>16*self.scale:font.setPointSizeF(font.pointSizeF()-1)
        self.big.setFont(font);self.badge.setText(view['badge']);self.badge.setVisible(bool(view['badge']))
        self.progress.setValue(view['fraction']);self.progress.setEnabled(view['online'])
        self.quick.setText('Elapsed '+view['elapsed']+'  ·  Finish '+view['finish_short'])
        self.total_caption.setText(view['total_title']+(' · incomplete' if view['incomplete'] else ' · provisional' if view['partial'] else ''))
        self.total.setText(view['elapsed'] if view['state']=='aborted' else view['total']);self.total.setStyleSheet('color:'+(completed if view['online'] and view['state']=='done' else text))
        self.finish_caption.setText('Finished at' if view['state']=='done' else 'Expected finish');self.finish.setText(view['finish'])
        self.started.setText('Started '+view['started']);self.current.setText(view['node']);self.current.setToolTip(view['node'])
        self.counts.setText(view['counts']);self.message.setText(view['message']);self.message.setToolTip(view['message'])
        key=(self.compact,self.scale,view['state'],view['node'],view['message'],view['badge'],view['title'],view['incomplete'],view['partial'])
        if key!=self._height_key:self._height_key=key;self.schedule_fit()
        self.sync.setText('Updated' if view['online'] else 'Offline');self.sync.setToolTip(self.url+'\n'+view['message'])
    def accept(self,payload):self.snapshot.accept(payload,time.monotonic());self.render()
    def fail(self,message):self.snapshot.fail(message);self.render()
    def toggle_top(self,checked):
        self.top=bool(checked);self.apply_flags();self.show();self.save()
    def toggle_compact(self):
        self.compact=not self.compact;self.details.setVisible(not self.compact);self.collapse.setText('▸' if self.compact else '▾')
        self.fit_content();self.save()
    def open_settings(self):
        dialog=SettingsDialog(self)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        new_url=server_url(dialog.url.text());changed=new_url!=self.url
        previous_scale=self.scale;previous_size=self.size()
        self.url=new_url;self.interval=dialog.interval.value();self.theme=dialog.theme.currentData();self.scale=dialog.scale.value()/100
        self.top=dialog.top.isChecked();self.border=dialog.border.isChecked();self.setWindowOpacity(dialog.opacity.value()/100)
        self.pin.setChecked(self.top);self.apply_style();self.apply_flags();self.layout().activate()
        ratio=self.scale/previous_scale;self.resize(round(previous_size.width()*ratio),round(previous_size.height()*ratio));self.show()
        if changed:self.snapshot.reset()
        self.render();self.save();self.poller.configure(self.url,self.interval)
    def save(self):
        for key,value in dict(server=self.url,interval=self.interval,theme=self.theme,scale=self.scale,compact=self.compact,top=self.top,border=self.border,
                opacity=self.windowOpacity(),width=self.width(),height=self.height()).items():self.settings.setValue(key,value)
        if not QGuiApplication.platformName().lower().startswith('wayland'):self.settings.setValue('geometry',self.saveGeometry())
        self.settings.sync()
    def closeEvent(self,event):
        if self.kwin:self.kwin.close()
        self.poller.stop();self.animation.stop();self.save();event.accept()

class SingleInstance(QObject):
    """Qt process lock plus an atomic activation marker; no tray or background daemon."""
    def __init__(self,name,parent=None,directory=None):
        super().__init__(parent)
        base=directory or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.RuntimeLocation) or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
        self.folder=Path(base)/'workflow-eta-panel';self.folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.marker=self.folder/(name+'.activate')
        self.lock=QLockFile(str(self.folder/(name+'.lock')));self.lock.setStaleLockTime(0)
        self.owned=False;self.seen='';self.timer=QTimer(self)
    def acquire(self):
        if self.lock.tryLock(0):
            self.owned=True
            self.seen=self.marker.read_text() if self.marker.exists() else ''
            return True
        if self.lock.error()!=QLockFile.LockError.LockFailedError:
            raise RuntimeError('Cannot access the single-instance lock. Check permissions for '+str(self.folder))
        import tempfile,uuid
        with tempfile.NamedTemporaryFile(mode='w',dir=self.folder,delete=False) as output:
            output.write(uuid.uuid4().hex);temporary=output.name
        os.replace(temporary,self.marker)
        return False
    def bind(self,window):
        def activate():
            try:token=self.marker.read_text() if self.marker.exists() else ''
            except OSError:return
            if token and token!=self.seen:
                self.seen=token;window.showNormal();window.raise_();window.activateWindow()
        self.timer.timeout.connect(activate);self.timer.start(500)
        QApplication.instance().aboutToQuit.connect(self.close)
    def close(self):
        self.timer.stop()
        if self.owned:self.lock.unlock();self.owned=False


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--version',action='version',version='Workflow ETA Panel '+VERSION);parser.add_argument('--url');parser.add_argument('--compact',action='store_true');parser.add_argument('--demo',action='store_true');args=parser.parse_args()
    app=QApplication(['workflow-eta-panel']);app.setApplicationName('workflow-eta-panel');app.setOrganizationName('WorkflowETA');app.setDesktopFileName('workflow-eta-panel')
    icon=QIcon(str(Path(__file__).with_name('workflow-eta-panel.svg')));app.setWindowIcon(icon)
    settings=QSettings('WorkflowETA','workflow-eta-panel-demo') if args.demo else QSettings()
    if args.url:
        try:settings.setValue('server',server_url(args.url))
        except ValueError as error:parser.error(str(error))
    if args.compact:settings.setValue('compact',True)
    guard=SingleInstance('workflow-eta-panel-'+str(os.getuid())+('-demo' if args.demo else ''),app)
    if not guard.acquire():return 0
    panel=Panel(settings,start_polling=not args.demo)
    if args.demo:
        demo={'state':'running','prompt_id':'demo','elapsed':246,'remaining':830,'expected_total':1076,'start_ts':time.time()-246,
            'node_class':'SamplerCustomAdvanced','node':'469','nodes_done':9,'nodes_total':14,'nodes_cached':5,'confidence':'estimated','fraction':.23,
            'eta_detail':{'provisional_remaining':830,'unpriced_nodes':[],'timing_message':'Sampling speed measured from recent steps','provisional_notes':[]}}
        panel.accept(demo)
        panel.demo_timer=QTimer(panel);panel.demo_timer.setInterval(1000)
        def demo_tick():
            elapsed=time.time()-demo['start_ts'];remaining=max(0,1076-elapsed)
            demo.update(elapsed=elapsed,remaining=remaining,fraction=elapsed/1076)
            demo['eta_detail']['provisional_remaining']=remaining
            if remaining==0:demo.update(state='done',finish_ts=time.time())
            panel.accept(demo)
        panel.demo_timer.timeout.connect(demo_tick);panel.demo_timer.start()
    guard.bind(panel);panel.show();result=app.exec()
    panel.deleteLater();app.sendPostedEvents(None,QEvent.Type.DeferredDelete);guard.close();return result

if __name__=='__main__':raise SystemExit(main())
