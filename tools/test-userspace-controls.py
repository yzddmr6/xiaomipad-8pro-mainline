# SPDX-License-Identifier: MIT
from pathlib import Path
import runpy,unittest,tempfile,json,types
from unittest.mock import Mock,patch
P=Path(__file__).resolve().parents[1]
class Charge(unittest.TestCase):
 def test_standard_attribute_and_missing_hardware(self):
  apply=runpy.run_path(str(P/'device/charge-policy/usr/libexec/tablet-charge-behaviour'))['apply']
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);b=root/'battery';b.mkdir();(b/'type').write_text('Battery\n')
   with self.assertRaises(RuntimeError):apply('inhibit-charge',root)
   (b/'charge_behaviour').write_text('[auto] inhibit-charge\n');apply('inhibit-charge',root);self.assertEqual((b/'charge_behaviour').read_text(),'inhibit-charge\n')
   (b/'charge_behaviour').write_text('auto [inhibit-charge]\n');apply('auto',root);self.assertEqual((b/'charge_behaviour').read_text(),'auto\n')
   with self.assertRaises(ValueError):apply('../../other',root)
 def test_preference_survives_a_new_invocation(self):
  module=runpy.run_path(str(P/'device/charge-policy/usr/libexec/tablet-charge-behaviour'))
  main=module['main'];namespace=main.__globals__
  with tempfile.TemporaryDirectory() as d, patch.dict(namespace, STATE=Path(d)/'behaviour'), patch.dict(namespace, apply=Mock()) as ns:
   with patch('sys.argv',['helper','inhibit-charge']):main()
   self.assertEqual(namespace['STATE'].read_text(),'inhibit-charge\n')
   with patch('sys.argv',['helper','restore']):main()
   self.assertEqual(namespace['apply'].call_args.args,('inhibit-charge',))
class Speaker(unittest.TestCase):
 def setUp(self):
  self.module=runpy.run_path(str(P/'device/audio/speaker-rotation/speaker-rotation.py'));self.service=self.module['SpeakerRotation'].__new__(self.module['SpeakerRotation']);self.service.config=json.loads((P/'packaging/piano-ubuntu-desktop/usr/share/piano/speaker-rotation/config.json').read_text());self.service.last_node=None;self.service.last_transform=None
 def test_mutter_uses_builtin_not_primary_external(self):
  self.service.proxy=Mock();self.service.proxy.call_sync.return_value.unpack.return_value=(1,[(('DSI-1','v','p','s'),[],{'is-builtin':True})],[(0,0,1,0,True,[('HDMI-1','v','p','s')],{}),(0,0,1,3,False,[('DSI-1','v','p','s')],{})],{})
  self.assertEqual(self.service.internal_transform(),3)
 def test_four_orientations_and_recreated_node(self):
  for transform,expected in enumerate([(0,1,2,3),(1,3,0,2),(3,2,1,0),(2,0,3,1)]):
   with patch('subprocess.run') as run:
    run.return_value.stdout=json.dumps([{'id':14,'type':'PipeWire:Interface:Node','info':{'props':{'object.serial':54,'node.name':self.service.config['control_node']}}}]);self.service.set_matrix(transform)
    params=json.loads(run.call_args.args[0][-1])['params'];gains=params[1::2];self.assertEqual([gains[i*4:(i+1)*4].index(1) for i in range(4)],list(expected))
    self.service.set_matrix(transform);self.assertEqual(run.call_count,3) # second call only dumps
    run.return_value.stdout=run.return_value.stdout.replace('54','55');self.service.set_matrix(transform);self.assertEqual(run.call_count,5)
if __name__=='__main__':unittest.main()
