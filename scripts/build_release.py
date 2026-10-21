"""Build installable platform ZIPs without local settings or generated meshes."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = {
    'windows-x64': 'gmsh-4.15.0-py2.py3-none-win_amd64.whl',
    'macos-arm64': 'gmsh-4.15.0-py2.py3-none-macosx_12_0_arm64.whl',
}
FILES = (
    'MSHExport.py', 'MSHExport.manifest', 'ScriptIcon.png',
    'export_types.py', 'export_workflow.py', 'fem_model.py', 'fusion_export.py',
    'gmsh_model.py', 'gmsh_support.py', 'msh_settings.py', 'seam_repair.py',
    'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'RELEASE_NOTES.md',
    'FusionScreenshot.png',
)


def _write(archive, name, data, stored=False):
    # Stable timestamps and permissions make identical inputs reproducible.
    info = zipfile.ZipInfo('MSHExport/' + name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    archive.writestr(info, data)


def build_release(root=ROOT, output=None):
    root = Path(root)
    output = Path(output) if output is not None else root / 'dist'
    manifest = json.loads((root / 'MSHExport.manifest').read_text(encoding='utf-8'))
    version = manifest['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Set a numeric major.minor.patch manifest version before packaging')
    if manifest['iconFilename'] not in FILES:
        raise ValueError('Manifest icon must be included in the package')
    resources = sorted(path.relative_to(root).as_posix()
                       for path in (root / 'Resources').rglob('*.png'))
    if not resources:
        raise ValueError('Toolbar resources are missing')
    # Read and validate every input before creating any artifacts.
    files = {name: (root / name).read_bytes() for name in (*FILES, *resources)}
    for name, data in files.items():
        if name.endswith('.py'):
            compile(data, name, 'exec')
    wheels = {}
    for platform, wheel_name in PLATFORMS.items():
        wheel = root / 'wheelhouse' / wheel_name
        with zipfile.ZipFile(wheel) as archive:
            license_path = next(name for name in archive.namelist()
                                if name.endswith('/share/doc/gmsh/LICENSE.txt'))
            license_text = archive.read(license_path)
        wheels[platform] = (wheel_name, wheel.read_bytes(), license_text)
    output.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for platform, (wheel_name, wheel_data, license_text) in wheels.items():
        path = output / f'FusionToMSH-v{version}-{platform}.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, data in sorted(files.items()):
                _write(archive, name, data)
            _write(archive, 'wheelhouse/' + wheel_name, wheel_data, stored=True)
            _write(archive, 'licenses/Gmsh-LICENSE.txt', license_text)
        with zipfile.ZipFile(path) as archive:
            bad_file = archive.testzip()
            if bad_file:
                raise ValueError(f'Corrupt release entry: {bad_file}')
        artifacts.append(path)
    checksums = output / 'SHA256SUMS.txt'
    checksums.write_text(''.join(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n'
                                for path in artifacts), encoding='utf-8')
    return [*artifacts, checksums]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    for artifact in build_release(output=args.output):
        print(artifact)
