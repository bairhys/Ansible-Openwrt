"""Exercise rollback retry guards against temporary archives and a fake service."""
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TASKS = yaml.safe_load((ROOT / 'roles/network_rollback/tasks/main.yml').read_text())


class RollbackResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='openwrt-rollback-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.armed = self.root / 'network-change-rollback.armed'
        self.pointer = self.root / 'network-change-rollback.archive'
        self.archive = self.root / 'network-backup-original.tar.gz'
        self.running = self.root / 'running'
        self.log = self.root / 'service.log'
        self.service = self.root / 'service'
        self.service.write_text(f'''#!/bin/sh
case "$1" in
    status) test -f '{self.running}' ;;
    enable|restart) echo "$1" >> '{self.log}' ;;
    *) exit 1 ;;
esac
''')
        self.service.chmod(0o700)

    def script(self, name):
        task = next(t for t in TASKS if t['name'] == name)
        text = task['ansible.builtin.raw']
        return text.replace('/root/', str(self.root) + '/').replace(
            '/etc/init.d/network-change-rollback', str(self.service))

    def run_task(self, name):
        return subprocess.run(['sh'], input=self.script(name), text=True, capture_output=True)

    def inspect(self):
        return self.run_task('Validate an existing rollback before resuming a failed deployment')

    def arm(self):
        return self.run_task('Arm rollback for thirty minutes including across reboots')

    def make_pending(self, seconds=900):
        with tarfile.open(self.archive, 'w:gz') as archive:
            body = b'original known-good configuration\n'
            info = tarfile.TarInfo('etc/config/network')
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
        self.pointer.write_text(str(self.archive) + '\n')
        self.armed.write_text(str(int(time.time()) + seconds) + '\n')
        self.running.touch()

    def test_fresh_run_selects_new_snapshot(self):
        result = self.inspect()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'new')

    def test_retry_preserves_original_snapshot_and_renews_timer(self):
        self.make_pending()
        original = self.archive.read_bytes()
        pointer = self.pointer.read_bytes()
        result = self.inspect()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'reuse')
        result = self.arm()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(int(self.armed.read_text()), int(time.time()) + 1790)
        self.assertEqual(self.archive.read_bytes(), original)
        self.assertEqual(self.pointer.read_bytes(), pointer)
        self.assertEqual(self.log.read_text().splitlines(), ['enable', 'restart'])
        for name in ('Back up configuration and shaper files with restricted permissions',
                     'Install persistent rollback worker', 'Install rollback service'):
            task = next(t for t in TASKS if t['name'] == name)
            self.assertEqual(task['when'], "network_rollback_state.stdout | trim == 'new'")

    def test_expired_or_imminent_rollback_is_not_renewed(self):
        for seconds in (-10, 30):
            with self.subTest(seconds=seconds):
                self.make_pending(seconds)
                deadline = self.armed.read_bytes()
                result = self.inspect()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Let it finish', result.stderr)
                self.assertNotEqual(self.arm().returncode, 0)
                self.assertEqual(self.armed.read_bytes(), deadline)
                self.assertFalse(self.log.exists())

    def test_corrupt_or_missing_archive_is_not_replaced(self):
        for state in ('corrupt', 'missing'):
            with self.subTest(state=state):
                self.make_pending()
                if state == 'corrupt':
                    self.archive.write_bytes(b'not an archive')
                else:
                    self.archive.unlink()
                result = self.inspect()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('missing or corrupt', result.stderr)
                self.assertTrue(self.armed.exists())
                self.assertFalse(self.log.exists())

    def test_stopped_worker_requires_recovery(self):
        self.make_pending()
        self.running.unlink()
        result = self.inspect()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('service is not running', result.stderr)
        self.assertTrue(self.armed.exists())

    def test_malformed_deadline_is_not_renewed(self):
        self.make_pending()
        self.armed.write_text('invalid\n')
        result = self.inspect()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('deadline is invalid', result.stderr)
        self.assertEqual(self.armed.read_text(), 'invalid\n')


if __name__ == '__main__':
    unittest.main()
