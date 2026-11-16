#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Small network/security fault tests; transaction acceptance uses apt-scenarios."""
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

source=Path(os.environ.get('UPDATE_SOURCE',Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(source/'usr/lib/python3/dist-packages'))
spec=importlib.util.spec_from_file_location('core',source/'usr/lib/mainline-update/core.py')
core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)
DATA=b'0123456789abcdef'*(1<<17)
class Handler(http.server.BaseHTTPRequestHandler):
    requests=[]
    drop_once=False
    def log_message(self,*args):pass
    def do_GET(self):
        offset=int(self.headers.get('Range','bytes=0-').split('=')[1].split('-')[0])
        self.requests.append(offset)
        self.send_response(206 if offset else 200)
        self.send_header('Content-Range',f'bytes {offset}-{len(DATA)-1}/{len(DATA)}')
        self.send_header('Content-Length',str(len(DATA)-offset));self.end_headers()
        block=DATA[offset:]
        if Handler.drop_once:
            block=block[:1048576];Handler.drop_once=False
        try:self.wfile.write(block)
        except BrokenPipeError:pass
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
class Offline(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.updater=object.__new__(core.Updater);self.updater.progress=None
        self.asset=dict(name='fixture.zip',size=len(DATA),browser_download_url=f'http://127.0.0.1:{server.server_port}/fixture.zip')
    def tearDown(self):self.tmp.cleanup()
    def test_resume(self):
        Handler.drop_once=True
        with self.assertRaises(core.UpdateError):
            self.updater._download(self.asset,self.root,hashlib.sha256(DATA).hexdigest())
        part=self.root/'fixture.zip.part'
        self.assertEqual(part.stat().st_size,1048576)
        output=self.updater._download(self.asset,self.root,hashlib.sha256(DATA).hexdigest())
        self.assertEqual(output.read_bytes(),DATA);self.assertEqual(Handler.requests[-1],1048576)
    def test_checksum_mismatch(self):
        with self.assertRaises(core.UpdateError):self.updater._download(self.asset,self.root,'0'*64)
        self.assertFalse((self.root/'fixture.zip').exists())
    def test_offline(self):
        self.asset['browser_download_url']='http://127.0.0.1:1/fixture.zip'
        with self.assertRaises(core.UpdateError):self.updater._download(self.asset,self.root,'0'*64)
        self.assertFalse((self.root/'fixture.zip').exists())
    def test_private_directory(self):
        self.root.chmod(0o777)
        with self.assertRaises(core.UpdateError):core._secure_directory(self.root)
    def test_polkit_admin(self):
        policy=ET.parse(source/'usr/share/polkit-1/actions/org.mainline.update.policy')
        for action in policy.findall('action'):
            self.assertEqual(action.findtext('defaults/allow_active'),'auth_admin_keep')
            self.assertEqual(action.findtext('defaults/allow_inactive'),'no')
            self.assertEqual(action.findtext('defaults/allow_any'),'no')
    def test_rollback_transaction_scope(self):
        self.updater.project={'product':'piano'}
        prepared={'new_batch':'/cache/piano-2.0'}
        with mock.patch.object(Path,'read_text',return_value='{"to":"piano-1.0"}'):
            self.assertFalse(self.updater._has_transaction(prepared))
        with mock.patch.object(Path,'read_text',return_value='{"to":"piano-2.0"}'):
            self.assertTrue(self.updater._has_transaction(prepared))
    def test_update_notes_section(self):
        self.updater.project={'notes_heading':'更新'}
        self.assertEqual(self.updater._release_notes({'body':'## 其他\n不显示\n## 更新\n实际更新\n## 安装\n不显示'}),'实际更新')
    def test_last_json(self):
        self.assertEqual(core.Updater._last_json('log\n{"phase":"upgraded","nested":{"x":1}}\n{"remove":[],"freed_bytes":7}'),dict(remove=[],freed_bytes=7))
if __name__=='__main__':
    unittest.main()
