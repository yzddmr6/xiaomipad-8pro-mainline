#!/usr/bin/env python3
"""Set only TESTSIGNING on BCD application objects, using Linux hivex."""
import sys
import hivex

h = hivex.Hivex(sys.argv[1], write=True)
objects = h.node_get_child(h.root(), "Objects")
changed = []
for obj in h.node_children(objects):
    description = h.node_get_child(obj, "Description")
    value = h.node_get_value(description, "Type") if description else 0
    if not value:
        continue
    kind, data = h.value_value(value)
    if int.from_bytes(data, "little") >> 28 != 1:
        continue
    elements = h.node_get_child(obj, "Elements") or h.node_add_child(obj, "Elements")
    node = h.node_get_child(elements, "16000049") or h.node_add_child(elements, "16000049")
    h.node_set_value(node, {"key": "Element", "t": 3, "value": b"\x01"})
    changed.append(h.node_name(obj))
if not changed:
    raise SystemExit("No BCD application objects: refusing to claim a patched BCD")
h.commit(None)
print("testsigning:", ", ".join(changed))
