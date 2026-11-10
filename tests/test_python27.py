"""Run under Python 2.7 as well as Python 3: method binding differs."""
from __future__ import absolute_import

import os
import sys
import unittest

SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      'python', 'gui', 'mods', 'mod_multik.py')


def load_mod():
    if sys.version_info[0] == 2:
        import imp
        return imp.load_source('multik_python27_test', SOURCE)
    import importlib.util
    spec = importlib.util.spec_from_file_location('multik_python27_test', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Python27Tests(unittest.TestCase):
    def setUp(self):
        self.mod = load_mod()
        self.addCleanup(self.mod.fini)

    def test_hook_restores_method_with_native_python27_binding(self):
        class Native(object):
            def call(self, value):
                return value + 1
        original = Native.call
        self.mod._enabled = True
        self.mod._hook(Native, 'call', lambda base, self, value: base(self, value) * 2)
        self.assertEqual(Native().call(3), 8)
        self.mod.fini()
        self.assertEqual(Native().call(3), 4)
        self.assertEqual(Native.call, original)

    def test_version_preserves_numeric_tail(self):
        self.assertEqual(self.mod._client_version('eu_2.4.1_3', 'ASIA'), 'asia_2.4.1_3')
        self.assertEqual(self.mod._client_version('unknown', 'ASIA'), 'unknown')


if __name__ == '__main__':
    unittest.main()
