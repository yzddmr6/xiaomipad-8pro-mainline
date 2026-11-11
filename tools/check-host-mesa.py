#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check hardware rendering in real consumer environments; missing evidence fails.

Run as root with --session-pid pointing to the active graphical user's shell.
--virgl-command is a JSON argv array executing glxinfo -B inside a running
GunyahVM guest, and --virgl-pid is its host GPU process. No VM is started here.
The WebKit check creates and closes its own small window in that session.
"""
import argparse
import ctypes as C
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys


def egl_probe():
    egl = C.CDLL('libEGL.so.1'); gl = C.CDLL('libGLESv2.so.2')
    def api(lib, name, restype, *args):
        fn = getattr(lib, name); fn.restype = restype; fn.argtypes = list(args); return fn
    pointer, integer = C.c_void_p, C.c_int
    get = api(egl, 'eglGetPlatformDisplay', pointer, C.c_uint, pointer, C.POINTER(integer))
    display = get(0x31DD, None, None)
    init = api(egl, 'eglInitialize', C.c_uint, pointer, C.POINTER(integer), C.POINTER(integer))
    if not init(display, None, None): raise RuntimeError('EGL initialization failed')
    api(egl, 'eglBindAPI', C.c_uint, C.c_uint)(0x30A0)
    attrs = (integer * 11)(0x3033,1,0x3040,4,0x3024,8,0x3023,8,0x3022,8,0x3038)
    config, count = pointer(), integer()
    if not api(egl,'eglChooseConfig',C.c_uint,pointer,C.POINTER(integer),C.POINTER(pointer),integer,C.POINTER(integer))(display,attrs,C.byref(config),1,C.byref(count)) or not count.value:
        raise RuntimeError('no EGL config')
    size = (integer * 5)(0x3057,16,0x3056,16,0x3038)
    surface = api(egl,'eglCreatePbufferSurface',pointer,pointer,pointer,C.POINTER(integer))(display,config,size)
    context = api(egl,'eglCreateContext',pointer,pointer,pointer,pointer,C.POINTER(integer))(display,config,None,(integer*3)(0x3098,2,0x3038))
    if not context or not surface or not api(egl,'eglMakeCurrent',C.c_uint,pointer,pointer,pointer,pointer)(display,surface,surface,context):
        raise RuntimeError('EGL context creation failed')
    renderer = api(gl,'glGetString',C.c_char_p,C.c_uint)(0x1F01).decode()
    api(gl,'glClearColor',None,C.c_float,C.c_float,C.c_float,C.c_float)(1,0,0,1)
    api(gl,'glClear',None,C.c_uint)(0x4000)
    pixel = (C.c_ubyte*4)()
    api(gl,'glReadPixels',None,integer,integer,integer,integer,C.c_uint,C.c_uint,pointer)(8,8,1,1,0x1908,0x1401,pixel)
    maps = Path('/proc/self/maps').read_text()
    print(json.dumps(dict(renderer=renderer,pixel=list(pixel),maps=sorted(set(re.findall(r'/\S*(?:libgallium|_dri)[^\s]*',maps))))))
    api(egl,'eglTerminate',C.c_uint,pointer)(display)
    return 0 if list(pixel)==[255,0,0,255] else 1


def webkit_probe():
    import gi
    gi.require_version('Gtk','4.0'); gi.require_version('WebKit','6.0')
    from gi.repository import Gtk, WebKit, GLib
    app = Gtk.Application(application_id='org.mainline.GraphicsCheck')
    result = {'error':'WebKit timed out'}
    def activate(app):
        window=Gtk.ApplicationWindow(application=app);window.set_title('Graphics check');window.set_default_size(96,96)
        view=WebKit.WebView();view.get_settings().set_enable_webgl(True);window.set_child(view)
        def evaluated(view, task, unused):
            try:
                value=view.evaluate_javascript_finish(task);result.clear();result.update(json.loads(value.to_string()))
                children=[]
                for proc in Path('/proc').glob('[0-9]*'):
                    try:
                        cmd=(proc/'cmdline').read_bytes()
                        status=(proc/'status').read_text()
                        parent = int(re.search(r'^PPid:\s+(\d+)',status,re.M)[1])
                        ancestors = set()
                        while parent > 1 and parent not in ancestors and parent != os.getpid():
                            ancestors.add(parent)
                            parent = int(re.search(r'^PPid:\s+(\d+)',(Path('/proc')/str(parent)/'status').read_text(),re.M)[1])
                        if parent == os.getpid() and b'WebKitWebProcess' in cmd and re.search(r'^Uid:\s+'+str(os.getuid())+r'\s',status,re.M):
                            maps=(proc/'maps').read_text();children.append(dict(pid=int(proc.name),maps=sorted(set(re.findall(r'/\S*(?:libgallium|_dri)[^\s]*',maps)))))
                    except (OSError,ValueError):pass
                result['web_processes']=children
            except Exception as error:result['error']=str(error)
            finally:window.close();app.quit()
        def loaded(view,event):
            if event==WebKit.LoadEvent.FINISHED:
                script='''JSON.stringify((()=>{let c=document.createElement('canvas');c.width=c.height=16;let g=c.getContext('webgl');if(!g)return {error:'no WebGL'};let x=g.getExtension('WEBGL_debug_renderer_info');g.clearColor(1,0,0,1);g.clear(g.COLOR_BUFFER_BIT);let p=new Uint8Array(4);g.readPixels(8,8,1,1,g.RGBA,g.UNSIGNED_BYTE,p);return {renderer:x?g.getParameter(x.UNMASKED_RENDERER_WEBGL):g.getParameter(g.RENDERER),pixel:Array.from(p)}})())'''
                view.evaluate_javascript(script,-1,None,None,None,evaluated,None)
        view.connect('load-changed',loaded);view.load_html('<!doctype html><title>Graphics check</title>',None);window.present()
        GLib.timeout_add_seconds(45,lambda:(app.quit(),False)[1])
    app.connect('activate',activate);app.run([]);print(json.dumps(result))
    return int('error' in result)


def standard_maps(pid):
    proc=Path('/proc')/str(pid)
    maps=(proc/'maps').read_text()
    paths=sorted(set(re.findall(r'/[^\s]*(?:libgallium|_dri|libEGL|libgbm)[^\s]*',maps)))
    return dict(pid=pid,paths=paths,pass_=bool(paths) and all(p.startswith(('/usr/lib/aarch64-linux-gnu/','/lib/aarch64-linux-gnu/')) for p in paths))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--probe',action='store_true',help=argparse.SUPPRESS)
    ap.add_argument('--webkit-probe',action='store_true',help=argparse.SUPPRESS)
    ap.add_argument('--session-pid',type=int)
    ap.add_argument('--virgl-pid',type=int)
    ap.add_argument('--virgl-command',help='JSON argv array; executes glxinfo -B in the real guest')
    ap.add_argument('--output',type=Path)
    a=ap.parse_args()
    if a.probe:return egl_probe()
    if a.webkit_probe:return webkit_probe()
    if os.geteuid()!=0 or not a.session_pid:ap.error('root and a real graphical session PID are required')
    proc=Path('/proc')/str(a.session_pid);uid=proc.stat().st_uid;user=pwd.getpwuid(uid)
    env=dict(item.decode().split('=',1) for item in (proc/'environ').read_bytes().split(b'\0') if b'=' in item)
    session={k:v for k,v in env.items() if k in ('DISPLAY','WAYLAND_DISPLAY','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS','XAUTHORITY','HOME','PATH','LANG')}
    script=str(Path(__file__).resolve());checks={}
    def execute(name,argv,renderer=True):
        try:
            r=subprocess.run(argv,capture_output=True,text=True,timeout=90)
            text=r.stdout+r.stderr
            passed=r.returncode==0 and (not renderer or bool(re.search(r'Adreno.*830',r.stdout,re.I)))
            checks[name]=dict(pass_=passed,returncode=r.returncode,stdout=r.stdout,stderr=r.stderr)
            return r
        except (OSError,subprocess.TimeoutExpired) as e:checks[name]=dict(pass_=False,error=str(e))
    def as_user(values,argv):return ['runuser','-u',user.pw_name,'--','env','-i',*[f'{k}={v}' for k,v in values.items()],*argv]
    checks['graphical-process']=standard_maps(a.session_pid)
    probes = [('graphical-session',as_user(session,['python3',script,'--probe'])),
              ('env-i',as_user({'PATH':'/usr/bin:/bin'},['python3',script,'--probe'])),
              ('sudo',['sudo','-n','env','-i','PATH=/usr/bin:/bin','python3',script,'--probe'])]
    for name, command in probes:
        result = execute(name, command)
        if result and result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                checks[name]['pass_'] &= bool(re.search(r'Adreno.*830',data['renderer'],re.I)) and data['pixel']==[255,0,0,255] and bool(data['maps']) and all(p.startswith(('/usr/lib/aarch64-linux-gnu/','/lib/aarch64-linux-gnu/')) for p in data['maps'])
            except (ValueError,KeyError):checks[name]['pass_']=False
    web=execute('webkit',as_user(session,['python3',script,'--webkit-probe']))
    if web and web.returncode==0:
        try:
            result=json.loads(web.stdout.strip().splitlines()[-1]);children=result['web_processes']
            checks['webkit']['pass_'] &= bool(re.search(r'Adreno.*830',result['renderer'],re.I)) and result['pixel']==[255,0,0,255] and bool(children) and any(c['maps'] for c in children) and all(p.startswith('/usr/lib/aarch64-linux-gnu/') for c in children for p in c['maps'])
        except (ValueError,KeyError,IndexError):checks['webkit']['pass_']=False
    vulkan=execute('vulkan',as_user(session,['vulkaninfo','--summary']))
    if vulkan:
        devices=re.findall(r'deviceName\s*=\s*([^\n]+)',vulkan.stdout)
        checks['vulkan']['a830_devices']=[d for d in devices if re.search(r'Adreno.*830',d,re.I)]
        checks['vulkan']['pass_'] &= len(checks['vulkan']['a830_devices'])==1
    if a.virgl_pid and a.virgl_command:
        checks['virgl-host']=standard_maps(a.virgl_pid)
        r=execute('virgl-guest',json.loads(a.virgl_command))
        if r:checks['virgl-guest']['pass_'] &= bool(re.search(r'OpenGL renderer string:.*virgl.*Adreno.*830',r.stdout,re.I))
    else:checks['virgl']=dict(pass_=False,error='real running guest command and host GPU PID required')
    result=dict(pass_=all(c['pass_'] for c in checks.values()),checks=checks)
    text=json.dumps(result,indent=2)+'\n'
    if a.output:a.output.write_text(text)
    print(text,end='');return int(not result['pass_'])

if __name__=='__main__':sys.exit(main())
