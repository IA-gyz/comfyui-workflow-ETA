import os
import io,json,os,sys,tempfile,time,threading,unittest,uuid
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from panel_model import Snapshot,server_url,server_port,valid_payload,duration,SUPPORTED_SCHEMA,THEMES as MODEL_THEMES,DEFAULT_THEME
from install import install,uninstall,exec_quote

class ModelTests(unittest.TestCase):
    def test_partial_and_completion(self):
        p=Snapshot();p.accept({'state':'running','elapsed':100,'remaining':10,'start_ts':1700000000,'expected_total':150,
            'lower_bound':True,'eta_detail':{'provisional_remaining':50,'unpriced_nodes':[{'node':'d'}],'provisional_notes':['First step includes startup']}},10)
        v=p.view(12);self.assertEqual(v['value'],'48s');self.assertEqual(v['elapsed'],'1m 42s');self.assertTrue(v['incomplete']);self.assertIn('startup',v['message']);self.assertIn('unpriced',v['badge'])
        p.fail('server closed');self.assertEqual(p.view(20)['value'],'—');self.assertEqual(p.view(20)['status'],'Disconnected')
        p.accept({'state':'done','elapsed':150,'expected_total':150,'finish_ts':1700000150},21)
        self.assertEqual(p.view(99)['value'],'2m 30s');self.assertEqual(p.view(99)['elapsed'],'2m 30s')
        p.accept({'state':'running','prompt_id':'new','elapsed':0,'eta_detail':{'provisional_remaining':None,'unpriced_nodes':[{}]}},100)
        self.assertEqual(p.view(100)['value'],'—')
    def test_stale_not_long_step(self):
        p=Snapshot();p.accept({'state':'running','elapsed':100,'remaining':600,'prompt_id':'a'},0)
        self.assertTrue(p.view(12)['stale']);self.assertEqual(p.view(12)['value'],'—')
        # Long initialization still counts elapsed time at the server; no progress event is needed.
        p.accept({'state':'running','elapsed':112,'remaining':600,'prompt_id':'a'},12)
        self.assertFalse(p.view(13)['stale']);self.assertEqual(p.view(13)['value'],'9m 59s')
        p.fail('Connection refused');self.assertEqual(p.view(14)['status'],'Disconnected')
    def test_schema_compatibility(self):
        base={'state':'running','elapsed':10,'remaining':20,'start_ts':1700000000,'eta_detail':{'provisional_remaining':20}}
        for extra in ({},{'schema':SUPPORTED_SCHEMA,'version':'0.2.34'},{'schema':0},{'schema':True},{'schema':'9'}):
            p=Snapshot();p.accept(dict(base,**extra),1);v=p.view(1)
            self.assertFalse(v['newer'],extra);self.assertNotIn('older',v['badge'])
        p=Snapshot();p.accept(dict(base,schema=SUPPORTED_SCHEMA,version='0.2.34'),1);self.assertEqual(p.view(1)['extension'],'Workflow ETA v0.2.34')
        p=Snapshot();p.accept(dict(base,version='<b>x</b>'),1);self.assertEqual(p.view(1)['extension'],'')  # markup never reaches a tooltip
        p=Snapshot();p.accept(dict(base,schema=SUPPORTED_SCHEMA+1,version='0.9.0'),1);v=p.view(1)
        self.assertTrue(v['newer']);self.assertEqual(v['badge'],'Panel older than extension');self.assertIn('Workflow ETA v0.9.0',v['message']);self.assertIn('update this panel',v['message'])
        self.assertEqual(v['value'],'20s')  # the countdown still shows; only a warning is added
        p.fail('gone');self.assertFalse(p.view(2)['newer'])  # nothing to warn about while offline
    def test_theme_comes_from_the_status_payload(self):
        base={'state':'running','elapsed':10,'remaining':20,'start_ts':1700000000,'eta_detail':{'provisional_remaining':20}}
        for theme in MODEL_THEMES:
            p=Snapshot();p.accept(dict(base,theme=theme),1);self.assertEqual(p.view(1)['theme'],theme)
        for bad in (None,'','nope',5,True,['forest'],{'forest':1},'FOREST'):
            p=Snapshot();p.accept(dict(base,theme=bad),1);self.assertIsNone(p.view(1)['theme'],bad)  # unknown values never reach the stylesheet
        p=Snapshot();p.accept(base,1);self.assertIsNone(p.view(1)['theme'])  # older extension: no theme field
        self.assertEqual(DEFAULT_THEME,next(iter(MODEL_THEMES)))
    def test_server_port(self):
        self.assertEqual(server_port('http://127.0.0.1:8288'),8288);self.assertEqual(server_port('http://localhost'),80);self.assertEqual(server_port('https://example.test'),443)
    def test_urls_and_bad_fields(self):
        self.assertEqual(server_url(' http://127.0.0.1:8188/ '),'http://127.0.0.1:8188')
        self.assertEqual(server_url('https://host/comfy/'),'https://host/comfy')
        for url in ['file:///etc/passwd','http://user:secret@host','http://host?token=a','http://host/#x','http://host:bad','http://']:
            with self.assertRaises(ValueError):server_url(url)
        with self.assertRaises(ValueError):valid_payload({'other':'data'})
        self.assertEqual(duration(float('nan')),'—');self.assertEqual(duration(True),'—')
        p=Snapshot();p.accept({'state':'aborted','elapsed':20},0);self.assertEqual(p.view(100)['total'],'20s')
    def test_launcher(self):
        with tempfile.TemporaryDirectory(prefix='eta panel ') as tmp:
            desktop=install(tmp);text=desktop.read_text();self.assertIn('StartupWMClass=workflow-eta-panel',text)
            self.assertIn(str(Path(__file__).resolve().parent/'workflow_eta_panel.py'),text);self.assertIn('Icon=workflow-eta-panel',text)
            self.assertFalse((Path(tmp)/'workflow-eta-panel'/'workflow_eta_panel.py').exists())  # runs in place, no stale copy
            venv=Path(tmp)/'venv/bin';venv.mkdir(parents=True);(venv/'python').symlink_to(sys.executable)
            desktop=install(tmp,str(venv/'python'));self.assertIn(str(venv/'python'),desktop.read_text())
            legacy=Path(tmp)/'workflow-eta-panel';legacy.mkdir(exist_ok=True);(legacy/'panel_model.py').write_text('old copy');keep=legacy/'user-note.txt';keep.write_text('keep')
            install(tmp);self.assertFalse((legacy/'panel_model.py').exists())  # a legacy copied file is cleaned up
            uninstall(tmp);self.assertFalse(desktop.exists());self.assertTrue(keep.exists())
        self.assertIn('%%',exec_quote('100% done'));self.assertIn('\\\\$',exec_quote('$HOME'))

try:
    from PySide6.QtWidgets import QApplication,QLabel
    from PySide6.QtCore import QSettings,QTimer,QEvent
    from PySide6.QtTest import QTest
    from workflow_eta_panel import Panel,StatusPoller,SingleInstance,SettingsDialog,THEMES
    QT=True
except ImportError:QT=False

@unittest.skipUnless(QT,'PySide6 required for Qt/network tests')
class QtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def wait(self,predicate,seconds=3):
        until=time.monotonic()+seconds
        while not predicate() and time.monotonic()<until:QTest.qWait(10)
        self.assertTrue(predicate(),'Timed out waiting for Qt event')
    def test_theme_compact_and_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings=QSettings(tmp+'/panel.ini',QSettings.Format.IniFormat)
            panel=Panel(settings,start_polling=False);panel.show();QTest.qWait(20)
            for key in THEMES:
                panel.theme=key;panel.apply_style();panel.accept({'state':'done','elapsed':123,'expected_total':123,'finish_ts':time.time()})
                self.assertIn('#806000' if key=='paper' else '#ffe066',panel.big.styleSheet())
            panel.scale=1.;panel.apply_style();panel.layout().activate();full_hint=panel.minimumSizeHint()
            panel.scale=.25;panel.apply_style();panel.layout().activate();small_hint=panel.minimumSizeHint()
            self.assertLess(small_hint.width(),full_hint.width()*.6);self.assertLess(small_hint.height(),full_hint.height()*.6)
            panel.save();small=Panel(settings,start_polling=False);self.assertEqual(small.scale,.25);small.close();small.deleteLater()
            panel.scale=1.;panel.apply_style();panel.layout().activate()
            old_height=panel.height();panel.toggle_compact();QTest.qWait(20);self.assertTrue(panel.details.isHidden());self.assertLess(panel.height(),old_height)
            panel.scale=1.4;panel.top=False;panel.save();panel.close()
            restored=Panel(settings,start_polling=False);self.assertTrue(restored.compact);self.assertEqual(restored.scale,1.4);self.assertFalse(restored.top);restored.close()
            panel.deleteLater();restored.deleteLater();self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    def test_panel_follows_the_node_theme(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings=QSettings(tmp+'/panel.ini',QSettings.Format.IniFormat)
            panel=Panel(settings,start_polling=False);panel.show();QTest.qWait(20)
            self.assertEqual(panel.theme,DEFAULT_THEME)
            for key,data in THEMES.items():
                panel.accept({'state':'idle','theme':key})
                self.assertEqual(panel.theme,key);self.assertIn(data[1],panel.styleSheet(),key)  # data[1] is the theme's background
            self.assertEqual(settings.value('theme'),'paper')  # the last one adopted is remembered
            panel.accept({'state':'idle','theme':'forest'})
            panel.accept({'state':'idle'});self.assertEqual(panel.theme,'forest')  # a payload without a theme keeps the current one
            panel.accept({'state':'idle','theme':'not-a-theme'});self.assertEqual(panel.theme,'forest')
            panel.fail('offline');self.assertEqual(panel.theme,'forest')  # losing the server keeps the colours too
            dialog=SettingsDialog(panel);self.assertFalse(hasattr(dialog,'theme'),'no theme picker: the node decides')
            self.assertTrue(any('Follows the Workflow ETA node (Forest)' in label.text() for label in dialog.findChildren(QLabel)))
            dialog.deleteLater();QTest.qWait(30);panel.save();panel.close()  # let the scheduled layout refits run before closing
            restored=Panel(settings,start_polling=False);self.assertEqual(restored.theme,'forest')  # next start opens in the last colours
            restored.close();QTest.qWait(30);panel.deleteLater();restored.deleteLater();self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    def test_kwin_keep_above_bridge(self):
        from workflow_eta_panel import KWinKeepAbove
        from PySide6.QtDBus import QDBusMessage
        from PySide6.QtQml import QJSEngine
        from unittest.mock import Mock
        bridge=KWinKeepAbove(None);calls=[]
        bridge.call=lambda *args:calls.append(args)
        reply=Mock();reply.type.return_value=QDBusMessage.MessageType.ReplyMessage;reply.arguments.return_value=[7]
        for enabled in (True,False):
            bridge.request(enabled);self.assertEqual(calls[-1][2],'unloadScript')
            bridge.load(reply);self.assertEqual(calls[-1][2],'loadScript')
            script=Path(calls[-1][3][0]).read_text()
            engine=QJSEngine();engine.evaluate("var windows=[{pid:"+str(os.getpid())+",caption:'Workflow ETA Panel',keepAbove:null},{pid:0,caption:'Other',keepAbove:null}];var workspace={windowList:function(){return windows;},windowAdded:{connect:function(f){}}};")
            result=engine.evaluate(script);self.assertFalse(result.isError(),result.toString())
            self.assertEqual(engine.evaluate('windows[0].keepAbove').toBool(),enabled)
            self.assertTrue(engine.evaluate('windows[1].keepAbove===null').toBool())
            bridge.run(reply);self.assertEqual(calls[-1][0],'/Scripting/Script7');bridge.ran(reply)
        errors=[];bridge.failed.connect(errors.append)
        bridge.request(True);reply.type.return_value=QDBusMessage.MessageType.ErrorMessage;reply.errorMessage.return_value='Denied';bridge.load(reply)
        self.assertEqual(errors,['Denied']);self.assertFalse(bridge.busy)
        bridge.close()
    def test_height_after_load_and_long_workflow_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings=QSettings(tmp+'/panel.ini',QSettings.Format.IniFormat)
            for scale in (.25,1.,2.):
                settings.clear();settings.setValue('height',2300);settings.setValue('scale',scale)
                panel=Panel(settings,start_polling=False);panel.show();QTest.qWait(30)
                self.assertLess(panel.height(),round(700*scale)+80)
                for state in ('running','done','running'):
                    panel.accept({'state':state,'elapsed':20,'remaining':200,'node_class':'Very long node name '*200,
                        'eta_detail':{'timing_message':'Long diagnostic message\n'*300}})
                    QTest.qWait(30);self.assertLess(panel.height(),round(750*scale)+100)
                panel.toggle_compact();QTest.qWait(30);self.assertTrue(panel.details.isHidden())
                self.assertLess(panel.height(),round(400*scale)+80)
                panel.close();panel.deleteLater();self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    def test_single_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            name='eta-test-'+uuid.uuid4().hex;first=SingleInstance(name,directory=directory);self.assertTrue(first.acquire())
            second=SingleInstance(name,directory=directory);self.assertFalse(second.acquire());first.close()
            third=SingleInstance(name,directory=directory);self.assertTrue(third.acquire());third.close()
    def test_async_poll_errors_reconnect_and_size_limit(self):
        state={'kind':'slow','requests':0,'paths':[]}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                state['requests']+=1;state['paths'].append(self.path)
                if state['kind']=='slow':time.sleep(.15)
                code=404 if state['kind']=='404' else 200
                if state['kind']=='html':body=b'<html>not JSON</html>'
                elif state['kind']=='large':body=b'x'*(8*1024*1024+1)
                else:body=json.dumps({'state':'running','elapsed':10,'remaining':20}).encode()
                self.send_response(code);self.end_headers()
                try:self.wfile.write(body)
                except (BrokenPipeError,ConnectionResetError):pass
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        poll=StatusPoller();results=[];errors=[];poll.received.connect(results.append);poll.failed.connect(errors.append)
        beats=[];timer=QTimer();timer.setInterval(10);timer.timeout.connect(lambda:beats.append(1));timer.start()
        url='http://127.0.0.1:'+str(server.server_port)
        try:
            poll.configure(url,10);poll.poll();self.wait(lambda:results)
            self.assertGreater(len(beats),2);self.assertEqual(state['requests'],1)
            for mode,expected in [('404','endpoint not found'),('html','non-JSON'),('large','8 MiB')]:
                state['kind']=mode;errors.clear();poll.poll();self.wait(lambda:errors);self.assertIn(expected,errors[0])
            state['kind']='ok';results.clear();poll.poll();self.wait(lambda:results)
            state['kind']='slow';errors.clear();poll.poll();poll.timeout();self.wait(lambda:errors);self.assertIn('4 seconds',errors[0])
            # Canceled old requests must not overwrite a new URL's status.
            state['kind']='ok';results.clear();errors.clear();poll.configure(url,10);poll.configure(url,10);self.wait(lambda:results);self.assertFalse(errors)
            # The panel never reads the inspector data or the log lines, so it asks for the compact status every time.
            self.assertEqual(set(state['paths']),{'/workflow/eta/status?compact=1'},state['paths'])
        finally:poll.stop();timer.stop();server.shutdown();server.server_close()

if __name__=='__main__':unittest.main(verbosity=2)
