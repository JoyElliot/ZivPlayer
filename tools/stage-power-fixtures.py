#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage and hash-check only the five generated benchmark files in app-private persistent storage."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import uuid

PACKAGE = 'io.github.joyelliot.zivplayer'
NAMES = {name + '-1080p24.mkv' for name in ['avc', 'hevc', 'hevc10', 'vp9', 'av1']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--source', type=Path, default=Path('native/out/power-fixtures'))
    parser.add_argument('--evidence', type=Path, default=Path('native/out/power-comparison-20260910'))
    args = parser.parse_args()
    manifest = json.loads((args.evidence / 'staged-fixtures.json').read_text(encoding='utf-8-sig'))
    assert {r['file'] for r in manifest} == NAMES and len(manifest) == len(NAMES)
    prefix = ['adb', '-s', args.serial]
    def command(*parts, timeout=30):
        return subprocess.check_output(prefix + list(parts), timeout=timeout)
    command('shell', 'am', 'force-stop', PACKAGE)
    command('shell', 'run-as', PACKAGE, 'mkdir', '-p', 'files/power-fixtures')
    staged = []
    for row in manifest:
        name = row['file']
        source = args.source / name
        assert source.stat().st_size == row['bytes']
        with source.open('rb') as stream:
            assert hashlib.file_digest(stream, 'sha256').hexdigest() == row['sha256']
        target = 'files/power-fixtures/' + name
        exists = subprocess.run(prefix + ['shell', 'run-as', PACKAGE, 'test', '-e', target], capture_output=True, timeout=20).returncode == 0
        if not exists:
            temporary = '/data/local/tmp/ziv-power-' + uuid.uuid4().hex + '.mkv'
            try:
                subprocess.run(prefix + ['push', str(source), temporary], check=True, timeout=240)
                command('shell', 'run-as', PACKAGE, 'cp', '-n', temporary, target, timeout=60)
                command('shell', 'run-as', PACKAGE, 'chmod', '600', target)
            finally:
                # Only remove the unique, fully named transfer file created by this invocation.
                command('shell', 'rm', '-f', temporary)
        actual = command('shell', 'run-as', PACKAGE, 'sha256sum', target, timeout=60).decode().split()[0]
        assert actual == row['sha256'], 'Do not overwrite an existing file with unexpected bytes: ' + target
        staged.append(dict(row, devicePath=target, verifiedSha256=actual))
        print('Verified private persistent fixture: ' + name, flush=True)
    record = dict(verifiedUtc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  storage='private-files', fixtures=staged)
    (args.evidence / 'persistent-fixtures.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
