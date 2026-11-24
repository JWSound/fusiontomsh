"""Exercise the result dialog without requiring Fusion's UI process."""
import ast
from datetime import datetime
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class FEMExportNoticeTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'MSHExport.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == '_show_fem_export_result')
        namespace = {'datetime': datetime, 'os': os,
                     'adsk': SimpleNamespace(doEvents=lambda: None)}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
        self.show = namespace['_show_fem_export_result']
        self.ui = Mock()

    def test_normal_success_has_no_repair_notice(self):
        self.show(self.ui, {'success': True, 'repaired_patches': []})
        self.ui.messageBox.assert_called_once()
        self.assertNotIn('repair', self.ui.messageBox.call_args.args[0].lower())

    def test_repair_is_reported_in_existing_success_dialog(self):
        self.show(self.ui, {'success': True, 'repaired_patches': [{'surface': 217}]})
        self.ui.messageBox.assert_called_once()
        self.assertIn('1 missing triangular patch was repaired', self.ui.messageBox.call_args.args[0])

    def test_failure_does_not_claim_successful_repair(self):
        self.show(self.ui, {'success': False, 'message': 'Meshing failed',
                            'repaired_patches': [{'surface': 217}]})
        self.ui.messageBox.assert_called_once_with('Meshing failed')
