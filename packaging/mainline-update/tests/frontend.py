#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Run under xvfb-run: widget contracts and optional-provider behavior."""
import importlib.machinery, json, os
from pathlib import Path
import sys,time
from unittest import mock
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Gtk, Adw, GLib
source=Path(__file__).resolve().parents[1]/'usr/libexec/mainline-update'
module=importlib.machinery.SourceFileLoader('update_ui',str(source)).load_module()
sys.argv=[str(source)]
app=module.App();app.preview={'phase':'idle','current':'1.4.1','version':'1.5.0','notes':'Changes','total_bytes':2868903936}
app.register(None);app.activate()
assert isinstance(app.window.get_content(),Adw.ToolbarView)
assert isinstance(app.window.get_content().get_content(),Adw.StatusPage)
assert not app.notice.get_visible()
assert app.notes_box.get_visible() and not app.notes.get_expanded()
assert app.buttons.get_first_child().has_css_class('suggested-action')
assert app.buttons.get_first_child().has_css_class('pill')
app.preview['metered']=True;app.render({})
assert app.notice.get_visible()
app.preview['metered']=False
for phase,index in [('downloading',0),('verifying',1),('extracting',2),('installing',3)]:
 app.render(dict(phase=phase,size=100,received=40,bytes_per_second=10))
 for i,(row,status) in enumerate(app.steps.values()):
  assert status.get_text()==('40%' if i==0 and index==0 else 'Done' if i<index else 'In progress' if i==index else 'Waiting'),(phase,i,status.get_text())
assert len(module.brief_message('noise\nerror: '+('x'*5000))) <= 100
for phase in ('prepared', 'failed_rollback'):
 app.render({'phase':phase,'error':'exact installed distribution package unavailable: libcamera0.7=0.7.0-1ubuntu2'})
 assert 'Software Updater' in app.page.get_description()
reason = 'Package configuration failed'
app.render({'phase':'failed_rollback','error':reason})
assert app.page.get_description() == reason
assert reason not in app.extra.get_text()
assert app.buttons.get_first_child().get_label()=='Retry installation'
assert app.buttons.get_first_child().get_next_sibling().has_css_class('destructive-action')
# GunyahVM 1.3.0 `list` returns config/status, and marks stale monitors interrupted.
fixture=[{'config':{'name':'Ubuntu work'},'status':{'state':'running'}},
         {'config':{'name':'Windows'},'status':{'state':'stopped'}},
         {'config':{'name':'Stale'},'status':{'state':'interrupted'}}]
with mock.patch('shutil.which',return_value='/usr/bin/gunyahvm'),mock.patch('subprocess.run',return_value=mock.Mock(stdout=json.dumps(fixture))):
 app.refresh_vms()
 end=time.monotonic()+5
 while not app.vms and time.monotonic()<end:
  GLib.MainContext.default().iteration(False);time.sleep(.01)
assert app.vms==['Ubuntu work']
assert not app.buttons.get_first_child().get_sensitive()
assert app.notice.get_visible() and 'Ubuntu work' in app.notice.get_text() and 'Windows' not in app.notice.get_text()
app.render({'phase':'rolling_back','display_phase':'interrupted'})
assert app.buttons.get_first_child().get_label()=='Retry rollback'
assert app.buttons.get_first_child().has_css_class('destructive-action')
app.render({'phase':'reboot_pending','display_phase':'reboot_pending','completion_notified':True})
assert app.page.get_title()=='Update complete'
assert app.buttons.get_first_child().get_label()=='Check for updates'
app.render({'phase':'downloading','version':'1.6.0'})
assert app.page.get_title()=='Downloading'
assert not app.view_state.get('completion_notified')
# A failed lookup must retry discovery even when no release version is known.
app.bus = object()
with mock.patch.object(app, 'call') as call:
 app.render({'phase':'failed_download','version':'','retry_discovery':True})
 app.buttons.get_first_child().emit('clicked')
 call.assert_called_once_with('GetOverview', ('',))
 call.reset_mock()
 app.render({'phase':'downloading','display_phase':'failed_download','version':'1.6.0'})
 app.buttons.get_first_child().emit('clicked')
 call.assert_called_once_with('Prepare', ('1.6.0',))
 app.render({'phase':'downloading','size':10,'received':3})
 assert app.page.get_title()=='Downloading'
# Standard status/description labels have no selectable cursor.
def walk(widget):
 yield widget
 child=widget.get_first_child()
 while child:
  yield from walk(child);child=child.get_next_sibling()
assert all(not w.get_selectable() for w in walk(app.page) if isinstance(w,Gtk.Label))
app.window.close()
print('PASS GNOME structure, button hierarchy, stage states, notes, metering, VM filtering, nonselectable labels')
