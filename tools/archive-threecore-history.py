#!/usr/bin/env python3
"""Verify the public redacted archive, read-only. Raw-device export is disabled.

The original exporter copied private device metadata unchanged. It is retired;
this entrypoint cannot read native/out or publish fresh device evidence.
"""
import argparse
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path,
                        default=Path('docs/archives/three-core-power-20261026'))
    args = parser.parse_args()
    root = args.archive
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['policy']['publicCopiesRedacted'] is True
    reports = members = bundles = 0
    for session in manifest['sessions']:
        for entry in session['reports']:
            data = (root / 'results' / session['directory'] / entry['path']).read_bytes()
            assert len(data) == entry['bytes'] and sha(data) == entry['sha256'], entry['path']
            reports += 1
        bundle = session['bundle']
        if bundle:
            data = (root / bundle['path']).read_bytes()
            assert len(data) == bundle['bytes'] and sha(data) == bundle['sha256'], bundle['path']
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
                entries = archive.getmembers()
                assert len(entries) == len(bundle['members'])
                for entry, expected in zip(entries, bundle['members']):
                    assert entry.isfile() and entry.name == expected['path']
                    content = archive.extractfile(entry).read()
                    assert len(content) == expected['bytes'] and sha(content) == expected['sha256'], entry.name
                    members += 1
            bundles += 1
    index = manifest['retainedEvidenceIndex']
    data = (root / index['path']).read_bytes()
    assert sha(data) == index['sha256']
    assert len(gzip.decompress(data).splitlines()) == index['archives']
    print(json.dumps({'reportsVerified': reports, 'bundlesVerified': bundles,
                      'membersVerified': members, 'publicCopiesRedacted': True,
                      'deviceAccess': False}, ensure_ascii=True))


if __name__ == '__main__':
    main()
