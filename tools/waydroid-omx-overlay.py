#!/usr/bin/python3
"""Apply the upstream orphaned OMX declaration workaround, without changing images.

Source: https://github.com/waydroid/waydroid/issues/2453
The installed image must declare OMX while providing no service executable.
"""
import argparse
from pathlib import Path
import re
import xml.etree.ElementTree as ET

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--rootfs',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
source=a.rootfs/'vendor/etc/vintf/manifest.xml'
text=source.read_text()
root=ET.fromstring(text)
orphans=[node for node in root.findall('hal') if node.findtext('name')=='android.hardware.media.omx']
if not orphans:raise SystemExit('No OMX declaration: workaround not applicable')
for folder in ('vendor/bin/hw','system/bin/hw','vendor/etc/init','system/etc/init'):
    if list((a.rootfs/folder).glob('*media.omx*')):
        raise SystemExit('OMX implementation/init exists: refusing to remove its declaration')
if a.output.exists():raise SystemExit('Existing overlay: retain and review it instead of overwriting')
removed=0
def remove(match):
    global removed
    node=ET.fromstring(match[0])
    if node.findtext('name')=='android.hardware.media.omx':removed+=1;return ''
    return match[0]
changed=re.sub(r'<hal\b[^>]*>.*?</hal>',remove,text,flags=re.S)
after=ET.fromstring(changed)
assert removed==len(orphans)
def content(n):return (n.tag,n.attrib,(n.text or '').strip(),[content(c) for c in n])
assert [content(n) for n in after]==[content(n) for n in root if n not in orphans]
a.output.parent.mkdir(parents=True,exist_ok=True)
a.output.write_text(changed)
print(f'Removed {removed} unimplemented OMX HAL declaration(s); image is unchanged: {a.output}')
