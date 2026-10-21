import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts import build_release


class ReleasePackagingTests(unittest.TestCase):
    def make_source(self, root):
        for name in build_release.FILES:
            path = root / name
            path.write_bytes(b'pass\n' if name.endswith('.py') else b'release file\n')
        (root / 'MSHExport.manifest').write_text(json.dumps({
            'version': '1.2.3', 'iconFilename': 'ScriptIcon.png',
        }), encoding='utf-8')
        icons = root / 'Resources' / 'MSHExport'
        icons.mkdir(parents=True)
        (icons / '32x32.png').write_bytes(b'icon')
        wheels = root / 'wheelhouse'
        wheels.mkdir()
        for name in build_release.PLATFORMS.values():
            with zipfile.ZipFile(wheels / name, 'w') as archive:
                archive.writestr('gmsh.py', 'pass\n')
                archive.writestr('gmsh-4.15.0.data/data/share/doc/gmsh/LICENSE.txt', 'upstream license')
        # These must never enter a distributable package.
        (root / '.msh_export_settings.json').write_text('private settings')
        (root / 'private.msh').write_text('mesh')
        cache = root / '.gmsh_wheels'
        cache.mkdir()
        (cache / 'private.txt').write_text('cache')

    def test_archives_are_complete_platform_specific_and_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_source(root)
            artifacts = build_release.build_release(root)
            self.assertEqual(len(artifacts), 3)
            digests = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in artifacts}
            checksums = artifacts[-1].read_text(encoding='utf-8')
            for artifact, wheel in zip(artifacts[:2], build_release.PLATFORMS.values()):
                with zipfile.ZipFile(artifact) as archive:
                    names = set(archive.namelist())
                    self.assertIn('MSHExport/MSHExport.py', names)
                    manifest = json.loads(archive.read('MSHExport/MSHExport.manifest'))
                    self.assertIn('MSHExport/' + manifest['iconFilename'], names)
                    self.assertIn('MSHExport/seam_repair.py', names)
                    self.assertIn('MSHExport/Resources/MSHExport/32x32.png', names)
                    self.assertEqual([n for n in names if n.endswith('.whl')],
                                     ['MSHExport/wheelhouse/' + wheel])
                    self.assertEqual(archive.read('MSHExport/licenses/Gmsh-LICENSE.txt'), b'upstream license')
                    self.assertTrue(all(n.startswith('MSHExport/') for n in names))
                    self.assertFalse(any('private' in n or '.gmsh_wheels' in n
                                         or '.msh_export_settings' in n for n in names))
                    self.assertIsNone(archive.testzip())
                self.assertIn(f'{digests[artifact.name]}  {artifact.name}', checksums)
            rebuilt = build_release.build_release(root)
            self.assertEqual(digests, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in rebuilt})

    def test_missing_runtime_fails_before_creating_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_source(root)
            (root / 'seam_repair.py').unlink()
            with self.assertRaises(FileNotFoundError):
                build_release.build_release(root)
            self.assertFalse((root / 'dist').exists())


if __name__ == '__main__':
    unittest.main()
