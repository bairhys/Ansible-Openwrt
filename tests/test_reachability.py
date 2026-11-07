"""Exercise host exclusion across plays without contacting real routers."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class ReachabilityTests(unittest.TestCase):
    def test_all_deployment_plays_require_a_successful_probe(self):
        for name in ('site.yml',):
            plays = yaml.safe_load((ROOT / name).read_text())
            self.assertEqual(plays[0]['import_playbook'], 'reachability.yml')
            for play in plays[1:]:
                self.assertIn(':&openwrt_playbook_accessible', play['hosts'])

    def test_inaccessible_hosts_remain_excluded_across_plays(self):
        with tempfile.TemporaryDirectory(prefix='openwrt-reachability-') as directory:
            directory = Path(directory)
            plugins = directory / 'connection_plugins'
            plugins.mkdir()
            # Use Ansible's real UNREACHABLE handling with a deterministic
            # connection failure, avoiding dependence on an actual SSH server.
            (plugins / 'simulated_offline.py').write_text('''
from ansible.plugins.connection import ConnectionBase
from ansible.errors import AnsibleConnectionFailure

class Connection(ConnectionBase):
    transport = 'simulated_offline'
    def _connect(self):
        raise AnsibleConnectionFailure('Simulated inaccessible OpenWrt host')
    def exec_command(self, cmd, in_data=None, sudoable=True):
        self._connect()
    def put_file(self, in_path, out_path):
        self._connect()
    def fetch_file(self, in_path, out_path):
        self._connect()
    def close(self):
        pass
''')
            failure_plugin = (plugins / 'simulated_offline.py').read_text()
            failure_plugin = failure_plugin.replace("transport = 'simulated_offline'", "transport = 'simulated_command_failure'")
            failure_plugin = failure_plugin.replace("raise AnsibleConnectionFailure('Simulated inaccessible OpenWrt host')", "return self")
            failure_plugin = failure_plugin.replace('        self._connect()', "        return (1, '', 'Simulated remote command failure')")
            (plugins / 'simulated_command_failure.py').write_text(failure_plugin)
            config = directory / 'ansible.cfg'
            config.write_text(f'[defaults]\nconnection_plugins = {plugins}\nlocal_tmp = {directory}/tmp\naction_plugins = {ROOT / 'action_plugins'}\nretry_files_enabled = false\n')
            inventory = directory / 'hosts.ini'
            inventory.write_text('''[openwrt_router]
online ansible_connection=local ansible_python_interpreter=/nonexistent/python
[openwrt_APs]
offline ansible_connection=simulated_offline
failed_command ansible_connection=simulated_command_failure
''')
            playbook = directory / 'test.yml'
            (directory / 'reachability.yml').write_text((ROOT / 'reachability.yml').read_text())
            plays = [{'import_playbook': str(directory / 'reachability.yml')}]
            for pattern in (
                'openwrt_router:openwrt_APs:&openwrt_playbook_accessible',
                'openwrt_router:&openwrt_playbook_accessible',
                'openwrt_APs:&openwrt_playbook_accessible',
            ):
                plays.append({
                    'hosts': pattern,
                    'gather_facts': False,
                    'any_errors_fatal': True,
                    'tasks': [
                        {'openwrt_wait_for_connection': {'timeout': 2}},
                        {'ansible.builtin.debug': {'msg': 'processed={{ inventory_hostname }}'}},
                    ],
                })
            playbook.write_text(yaml.safe_dump(plays))
            env = os.environ.copy()
            env['ANSIBLE_CONFIG'] = str(config)
            for check in (False, True):
                with self.subTest(check_mode=check):
                    command = [str(ROOT / 'venv/bin/ansible-playbook'), '-i', str(inventory), str(playbook)]
                    if check:
                        command.append('--check')
                    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
                    output = result.stdout + result.stderr
                    self.assertEqual(result.returncode, 0, output)
                    self.assertEqual(output.count('processed=online'), 2, output)
                    self.assertNotIn('processed=offline', output)
                    self.assertNotIn('processed=failed_command', output)
                    self.assertIn('Excluding offline from the rest of this run', output)
                    self.assertIn('Excluding failed_command from the rest of this run', output)


if __name__ == '__main__':
    unittest.main()
