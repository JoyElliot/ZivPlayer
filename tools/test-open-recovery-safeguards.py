"""Exercise device-local recovery decisions with local files; never invokes ADB."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

SOURCE = (Path(__file__).parent/'power-night-open-guard.sh').read_text(encoding='utf-8')


def function(name):
    return re.search(r'^'+name+r'\(\) \{\n.*?^\}\n', SOURCE, re.M | re.S)[0]


class RecoverySafeguards(unittest.TestCase):
    def shell(self, name, body, root):
        return subprocess.run(['bash', '-c', function(name)+'\n'+body],
            env=dict(os.environ, WATCH_ROOT=str(root)), capture_output=True, text=True)

    def test_cooling_poll_updates_do_not_mask_stall(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'manifest.tsv').write_text('leg\n'*4)
            result=self.shell('watch_active_runner', '''
RECOVERY_TRIGGER=waiting
printf 't1\tcooling\t1\tfirst\t90\n' > "$WATCH_ROOT/status.tsv"
watch_active_runner "$WATCH_ROOT" 100 || exit 10
printf 't2\tcooling\t1\tfirst\t89\n' > "$WATCH_ROOT/status.tsv"
watch_active_runner "$WATCH_ROOT" 1899 || exit 11
if watch_active_runner "$WATCH_ROOT" 1900; then exit 12; fi
test "$RECOVERY_TRIGGER" = runner_progress_timeout
''', root)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_leg_resets_phase_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'manifest.tsv').write_text('leg\n'*4)
            result=self.shell('watch_active_runner', '''
printf 't1\tcooling\t1\tfirst\t90\n' > "$WATCH_ROOT/status.tsv"
watch_active_runner "$WATCH_ROOT" 100 || exit 10
printf 't2\tcooling\t2\tsecond\t88\n' > "$WATCH_ROOT/status.tsv"
watch_active_runner "$WATCH_ROOT" 1800 || exit 11
watch_active_runner "$WATCH_ROOT" 1900 || exit 12
''', root)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_repeated_phase_changes_cannot_renew_stage_forever(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'manifest.tsv').write_text('leg\n'*4)
            result=self.shell('watch_active_runner', '''
RECOVERY_TRIGGER=waiting
i=0
while test "$i" -lt 26; do
    if test "$((i%2))" -eq 0; then phase=cooling; else phase=running; fi
    printf 't\t%s\t1\tfirst\t90\n' "$phase" > "$WATCH_ROOT/status.tsv"
    watch_active_runner "$WATCH_ROOT" "$((100+i*500))" || exit 10
    i=$((i+1))
done
if watch_active_runner "$WATCH_ROOT" 13000; then exit 11; fi
test "$RECOVERY_TRIGGER" = runner_progress_timeout
''', root)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_child_identity_is_not_proof_of_termination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            result=self.shell('child_identity_evidence_ok', '''
if child_identity_evidence_ok "$WATCH_ROOT"; then exit 10; fi
: > "$WATCH_ROOT/network-process-stat.txt"
printf 't\tcooling\t1\tfirst\t90\n' > "$WATCH_ROOT/events.tsv"
child_identity_evidence_ok "$WATCH_ROOT" || exit 11
printf 't\trunning\t1\tfirst\t90\n' >> "$WATCH_ROOT/events.tsv"
if child_identity_evidence_ok "$WATCH_ROOT"; then exit 12; fi
: > "$WATCH_ROOT/active-am-process-stat.txt"
mkdir "$WATCH_ROOT/control"
: > "$WATCH_ROOT/control/first.am-process-stat.txt"
child_identity_evidence_ok "$WATCH_ROOT" || exit 13
''', root)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_previous_leg_identity_cannot_cover_new_child_fork(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            result=self.shell('child_identity_evidence_ok', '''
: > "$WATCH_ROOT/network-process-stat.txt"
mkdir "$WATCH_ROOT/control"
printf first_identity > "$WATCH_ROOT/active-am-process-stat.txt"
printf first_identity > "$WATCH_ROOT/control/first.am-process-stat.txt"
printf 't1\trunning\t1\tfirst\t90\nt2\trunning\t2\tsecond\t89\n' > "$WATCH_ROOT/events.tsv"
if child_identity_evidence_ok "$WATCH_ROOT"; then exit 10; fi
printf second_identity > "$WATCH_ROOT/control/second.am-process-stat.txt"
if child_identity_evidence_ok "$WATCH_ROOT"; then exit 11; fi
cp "$WATCH_ROOT/control/second.am-process-stat.txt" "$WATCH_ROOT/active-am-process-stat.txt"
child_identity_evidence_ok "$WATCH_ROOT" || exit 12
''', root)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_clean_early_exit_can_precede_child_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'exit.json').write_text('{"cleanupSucceeded":true}\n')
            result=self.shell('child_identity_evidence_ok',
                'child_identity_evidence_ok "$WATCH_ROOT"', root)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
