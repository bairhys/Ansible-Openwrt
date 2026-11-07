"""Static checks for the deployment contract; run with the project venv."""
from pathlib import Path
import subprocess
import unittest
import yaml
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
VALUES = yaml.safe_load((ROOT / 'group_vars/all/unrestricted.yml').read_text())


def render(name):
    return Environment(undefined=StrictUndefined).from_string(
        (ROOT / 'roles/cake_autorate/templates' / name).read_text()
    ).render(**VALUES)


class ConfigurationTests(unittest.TestCase):
    def test_all_yaml_parses(self):
        paths = [ROOT / name for name in ('site.yml', 'reachability.yml', 'vault_example.yml')]
        paths += list((ROOT / 'roles').rglob('*.yml'))
        paths += list((ROOT / 'group_vars').rglob('*.yml'))
        for path in paths:
            if path.name == 'vault.yml':
                continue
            with self.subTest(path=path):
                yaml.safe_load(path.read_text())

    def test_shapers_are_separate_and_marks_do_not_overlap(self):
        self.assertNotEqual(VALUES['cake_upload_ifb'], VALUES['cake_download_ifb'])
        up = int(VALUES['cake_upload_mark'], 0)
        down = int(VALUES['cake_download_mark'], 0)
        mask = int(VALUES['cake_mark_mask'], 0)
        self.assertEqual(up & down, 0)
        self.assertEqual(up | down, mask)
        self.assertEqual(mask ^ 0xffffffff, int(VALUES['cake_mark_clear_mask'], 0))
        for name in ('sqm.j2', 'selective-cake.conf.j2', 'selective-cake.nft.j2'):
            self.assertTrue(render(name))

    def test_password_preflight_precedes_all_remote_changes(self):
        plays = yaml.safe_load((ROOT / 'site.yml').read_text())
        self.assertEqual(plays[0]['import_playbook'], 'reachability.yml')
        plays = plays[1:]
        self.assertEqual(plays[0]['tasks'][0]['ansible.builtin.import_role']['tasks_from'], 'preflight.yml')
        self.assertTrue(plays[0]['any_errors_fatal'])
        self.assertIn('network_rollback', plays[1]['roles'])
        self.assertEqual(plays[-1]['tasks'][-1]['ansible.builtin.import_role']['tasks_from'], 'disarm.yml')

    def test_both_vlan_modes_keep_existing_trunks(self):
        tasks = yaml.safe_load((ROOT / 'roles/unrestricted_network/tasks/main.yml').read_text())
        dsa = tasks[0]['community.openwrt.uci']['value']
        legacy = tasks[1]['community.openwrt.uci']['value']
        self.assertIn('iot_dsa_trunk_ports', dsa['ports'])
        self.assertIn('iot_swconfig_trunk_ports', legacy['ports'])
        self.assertEqual(legacy['vlan'], '{{ unrestricted_swconfig_vlan_index }}')

    def test_wifi_credentials_do_not_reuse_lan_secret(self):
        defaults = (ROOT / 'roles/wifi/defaults/main.yml').read_text()
        iface = yaml.safe_load(defaults)['wifi_unrestricted_iface']
        self.assertIn('vault_unrestricted_wifi_password', iface)
        self.assertNotIn('vault_wifi_password', iface)
        tasks = yaml.safe_load((ROOT / 'roles/wifi/tasks/main.yml').read_text())
        task = next(t for t in tasks if t['name'] == 'Configure unrestricted Wi-Fi interfaces')
        self.assertTrue(task['no_log'])
        self.assertEqual(task['community.openwrt.uci']['value']['network'], '{{ wifi_unrestricted_iface.network }}')

    def test_wireguard_does_not_own_lan(self):
        self.assertNotIn("add_list firewall.Wireguard.network='lan'", (ROOT / 'roles/wireguard/tasks/main.yml').read_text())

    def test_shell_syntax(self):
        paths = list((ROOT / 'roles/cake_autorate/files').glob('selective*'))
        paths += [ROOT / 'roles/cake_autorate/files/95-selective-cake']
        paths += list((ROOT / 'roles/network_rollback/files').iterdir())
        for path in paths:
            with self.subTest(path=path):
                subprocess.run(['sh', '-n', str(path)], check=True)
        subprocess.run(['sh', '-n'], input=render('selective-cake.conf.j2'), text=True, check=True)


if __name__ == '__main__':
    unittest.main()
