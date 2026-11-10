#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build INQ Multik using the same python/ + build.json layout as other INQ mods."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'python/gui/mods/mod_multik.py'
ARCHIVE_SCRIPT = 'res/scripts/client/gui/mods/mod_multik.pyc'
PY27_MAGIC = b'\x03\xf3\x0d\x0a'
PACKAGE_ID = 'me.inq.multik'
VERSION_RE = re.compile(r'^[0-9]+(?:\.[0-9]+){1,3}(?:[-.][A-Za-z0-9.-]+)?$')


def load_config(path):
    with path.open('r', encoding='utf-8-sig') as stream:
        config = json.load(stream)
    if not isinstance(config, dict):
        raise ValueError('Build configuration must be an object')
    return config


def required_text(section, key):
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Missing or invalid configuration field: %s' % key)
    return value.strip()


def verify_config(config):
    for section in ('software', 'game', 'info'):
        if not isinstance(config.get(section), dict):
            raise ValueError('Missing configuration section: %s' % section)
    info = config['info']
    for key in ('id', 'version', 'name', 'description'):
        required_text(info, key)
    if info['id'] != PACKAGE_ID:
        raise ValueError('Package id must be %s' % PACKAGE_ID)
    if not VERSION_RE.fullmatch(info['version']):
        raise ValueError('Invalid mod version: %s' % info['version'])
    game_version = required_text(config['game'], 'version')
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){3}', game_version):
        raise ValueError('Game version must use X.X.X.X format')


def compile_python(executable, destination):
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)
    version = subprocess.check_output(
        [executable, '-c',
         'import sys; sys.stdout.write("%d.%d" % sys.version_info[:2])']
    ).decode('ascii').strip()
    if version != '2.7':
        raise RuntimeError('WoT requires Python 2.7 bytecode; compiler is %s' % version)
    subprocess.check_call([
        executable, '-c',
        'import py_compile,sys; '
        'py_compile.compile(sys.argv[1],sys.argv[2],sys.argv[3],doraise=True)',
        str(SOURCE), str(destination), ARCHIVE_SCRIPT
    ])
    data = destination.read_bytes()
    if data[:4] != PY27_MAGIC:
        raise RuntimeError('Compiler produced unexpected Python bytecode')
    return data


def metadata(info):
    root = ET.Element('root')
    for key in ('id', 'version', 'name', 'description'):
        ET.SubElement(root, key).text = info[key]
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def write_entry(archive, name, data):
    entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    entry.compress_type = zipfile.ZIP_STORED
    entry.external_attr = 0o100644 << 16
    archive.writestr(entry, data)


def verify_package(path):
    with zipfile.ZipFile(path) as archive:
        if set(archive.namelist()) != {'meta.xml', ARCHIVE_SCRIPT}:
            raise RuntimeError('Package must contain metadata and exactly one runtime script')
        if any(item.compress_type != zipfile.ZIP_STORED for item in archive.infolist()):
            raise RuntimeError('WOTMOD entries must use ZIP_STORED')
        if archive.read(ARCHIVE_SCRIPT)[:4] != PY27_MAGIC:
            raise RuntimeError('Package does not contain Python 2.7 bytecode')
        ET.fromstring(archive.read('meta.xml'))
        bad = archive.testzip()
        if bad:
            raise RuntimeError('Corrupt package entry: %s' % bad)


def build(config, executable, distribute=False):
    verify_config(config)
    output = ROOT / 'build'
    output.mkdir(exist_ok=True)
    filename = '%s(%s).wotmod' % (config['info']['id'], config['info']['version'])
    package = output / filename
    # Compile in staging; never leave .pyc files next to tracked sources.
    with tempfile.TemporaryDirectory(prefix='multik-build-') as staging:
        bytecode = compile_python(executable, Path(staging) / 'mod_multik.pyc')
        staged_package = Path(staging) / filename
        with zipfile.ZipFile(staged_package, 'w') as archive:
            write_entry(archive, 'meta.xml', metadata(config['info']))
            write_entry(archive, ARCHIVE_SCRIPT, bytecode)
        verify_package(staged_package)
        package.write_bytes(staged_package.read_bytes())
    print('Created:', package.relative_to(ROOT))
    if distribute:
        distribution = output / (filename + '.zip')
        install_path = 'mods/%s/%s' % (config['game']['version'], filename)
        with zipfile.ZipFile(distribution, 'w') as archive:
            write_entry(archive, install_path, package.read_bytes())
        print('Created:', distribution.relative_to(ROOT))
    return package


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'build.json')
    parser.add_argument('--python2', help='Path to the Python 2.7 compiler')
    parser.add_argument('--version', help='Override the mod version for this build')
    parser.add_argument('--game-version', help='Override the target WoT version')
    parser.add_argument('--distribute', action='store_true', help='Also create an installation ZIP')
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        if args.version:
            config.setdefault('info', {})['version'] = args.version
        if args.game_version:
            config.setdefault('game', {})['version'] = args.game_version
        executable = (args.python2 or os.environ.get('PYTHON2') or
                      config.get('software', {}).get('python'))
        if not executable:
            raise ValueError('Set PYTHON2, --python2, or software.python in build.json')
        build(config, executable, args.distribute)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print('Build failed: %s' % exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
