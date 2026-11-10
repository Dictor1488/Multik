"""Run under Python 2.7 as well as Python 3: method binding differs."""
from __future__ import absolute_import

import os
import sys
import unittest
import shutil
import tempfile
import glob
import zipfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, 'python', 'gui', 'mods', 'mod_multik.py')


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

    @unittest.skipUnless(sys.version_info[0] == 2, 'Package contains Python 2.7 bytecode')
    def test_built_package_metadata_matches_runtime_version(self):
        import imp
        packages = glob.glob(os.path.join(ROOT, 'build', '*.wotmod'))
        self.assertEqual(len(packages), 1)
        staging = tempfile.mkdtemp(prefix='multik-verify-')
        try:
            with zipfile.ZipFile(packages[0]) as archive:
                meta = ET.fromstring(archive.read('meta.xml'))
                bytecode = archive.read('res/scripts/client/gui/mods/mod_multik.pyc')
            path = os.path.join(staging, 'mod_multik.pyc')
            with open(path, 'wb') as stream:
                stream.write(bytecode)
            runtime = imp.load_compiled('multik_release_payload', path)
            self.assertEqual(runtime.MOD_VERSION, meta.findtext('version'))
            self.assertEqual(runtime.MOD_ID, meta.findtext('id'))
        finally:
            shutil.rmtree(staging)


if __name__ == '__main__':
    unittest.main()
