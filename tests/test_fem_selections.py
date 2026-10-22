"""Selection lifecycle tests; actual Fusion replay is verified in-host."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'MSHExport.py'

class Input:
    def __init__(self, faces=()):
        self.faces = list(faces)
        self.hasFocus = False
    def clearSelection(self):
        self.faces.clear()
    def addSelection(self, face):
        self.faces.append(face)

class Inputs:
    isValid = True
    def __init__(self):
        self.items = {'fem_faces_1': Input([1, 2, 3]), 'fem_faces_2': Input([4])}
    def itemById(self, key):
        return self.items.get(key)

class SelectionLifecycleTests(unittest.TestCase):
    def setUp(self):
        names = {'_queue_fem_selection_restore', '_restore_pending_fem_selections'}
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        selected = ast.Module(body=[x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name in names], type_ignores=[])
        self.events = []
        self.app = NS(fireCustomEvent=lambda name: self.events.append(name) or True)
        self.env = {'adsk': NS(core=NS(Application=NS(get=lambda: self.app))),
                    'FEM_SELECTION_EVENT_ID': 'test', '_fem_selection_updates': [],
                    '_fem_tag_input_id': lambda prefix, g: f'fem_{prefix}_{g}',
                    '_selected_entities': lambda inp: list(inp.faces) if inp else []}
        exec(compile(selected, str(SOURCE), 'exec'), self.env)
        self.inputs = Inputs()
        self.state = NS(group_ids=[1, 2], pending_faces=None, restoring=False)
    def queue(self):
        self.env['_queue_fem_selection_restore'](self.inputs, self.state)
    def restore(self):
        self.env['_restore_pending_fem_selections'](self.inputs, self.state)
    def test_add_group_corrects_late_union_replay(self):
        self.queue()
        self.state.group_ids.append(3)
        self.state.pending_faces[3] = []
        self.inputs.items['fem_faces_3'] = Input()
        self.inputs.items['fem_faces_1'].faces.append(4)
        self.restore()
        self.assertEqual(self.inputs.items['fem_faces_1'].faces, [1, 2, 3])
        self.assertEqual(self.inputs.items['fem_faces_2'].faces, [4])
        self.assertEqual(self.inputs.items['fem_faces_3'].faces, [])
    def test_batch_rebuild_keeps_original_snapshot(self):
        self.queue()
        self.inputs.items['fem_faces_1'].faces.append(4)
        self.queue()
        self.assertEqual(len(self.events), 1)
        self.restore()
        self.assertEqual(self.inputs.items['fem_faces_1'].faces, [1, 2, 3])
    def test_later_user_changes_are_not_undone(self):
        self.queue()
        self.restore()
        self.inputs.items['fem_faces_1'].clearSelection()
        self.restore()
        self.assertEqual(self.inputs.items['fem_faces_1'].faces, [])
        self.queue()
        self.inputs.items['fem_faces_1'].faces.append(4)
        self.restore()
        self.assertEqual(self.inputs.items['fem_faces_1'].faces, [])
    def test_cancelled_dialog_is_not_accessed(self):
        self.queue()
        self.inputs.isValid = False
        self.restore()
        self.assertIsNone(self.state.pending_faces)
    def test_removed_group_is_not_restored(self):
        self.queue()
        self.state.group_ids.remove(2)
        self.state.pending_faces.pop(2)
        del self.inputs.items['fem_faces_2']
        self.inputs.items['fem_faces_1'].faces.append(4)
        self.restore()
        self.assertEqual(self.inputs.items['fem_faces_1'].faces, [1, 2, 3])
    def test_false_return_does_not_discard_a_deliverable_event(self):
        self.app.fireCustomEvent = lambda name: False
        self.queue()
        self.inputs.items['fem_faces_1'].faces.append(4)
        self.restore()
        self.assertEqual(self.inputs.items['fem_faces_1'].faces, [1, 2, 3])

if __name__ == '__main__':
    unittest.main()
