"""Isolated routing/IFB integration test. Never run in the host network namespace.

See tests/README.md. Uses production nft rules and helper, with TBF substituted
only when the host kernel lacks CAKE. No live router configuration is involved.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from test_unrestricted import ROOT, VALUES, render
import yaml

if 'UNRESTRICTED_PARENT_NETNS' not in os.environ:
    env = os.environ.copy()
    env['UNRESTRICTED_PARENT_NETNS'] = os.readlink('/proc/self/ns/net')
    sys.exit(subprocess.run(['unshare', '-Urn', sys.executable, __file__], env=env).returncode)
if os.geteuid() != 0 or os.readlink('/proc/self/ns/net') == os.environ['UNRESTRICTED_PARENT_NETNS']:
    sys.exit('Refusing to change the host network: namespace isolation failed.')

TC = shutil.which('tc')
NFT = shutil.which('nft')
if not NFT:
    sys.exit('nft is required; install it or add an extracted package to PATH.')


def run(*args, input=None):
    result = subprocess.run(args, input=input, text=True, capture_output=True)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, args, output=result.stdout, stderr=result.stderr)
    return result.stdout


def ip(*args):
    return run('ip', *args)


children = []
servers = []
work = tempfile.TemporaryDirectory(prefix='unrestricted-network-test-')
try:
    ip('link', 'set', 'lo', 'up')
    peers = {}
    specs = [
        ('wan', 'eth1', '198.18.0', '10', False),
        ('lan', 'br-lan.1', '192.168.1', '1', True),
        ('iot', 'br-lan.20', '192.168.20', '20', True),
        ('bypass', 'br-unrestricted', '192.168.30', '30', True),
        ('vpn', 'Wireguard', '172.16.1', '40', False),
    ]
    for name, device, subnet, v6id, bridge in specs:
        proc = subprocess.Popen(['unshare', '-n', 'sleep', '180'])
        children.append(proc)
        # Wait for the child to enter its new network namespace.
        for _ in range(100):
            if os.readlink(f'/proc/{proc.pid}/ns/net') != os.readlink('/proc/self/ns/net'):
                break
            time.sleep(.01)
        peer = ['nsenter', '-t', str(proc.pid), '-n']
        peers[name] = (peer, f'{subnet}.2', f'fd00:{v6id}::2')
        port = f'p-{name}' if bridge else device
        ip('link', 'add', port, 'type', 'veth', 'peer', 'name', f'c-{name}')
        ip('link', 'set', f'c-{name}', 'netns', str(proc.pid))
        if bridge:
            ip('link', 'add', device, 'type', 'bridge')
            ip('link', 'set', port, 'master', device)
            ip('link', 'set', port, 'up')
        ip('addr', 'add', f'{subnet}.1/24', 'dev', device)
        ip('-6', 'addr', 'add', f'fd00:{v6id}::1/64', 'dev', device, 'nodad')
        ip('link', 'set', device, 'up')
        run(*peer, 'ip', 'link', 'set', 'lo', 'up')
        run(*peer, 'ip', 'addr', 'add', f'{subnet}.2/24', 'dev', f'c-{name}')
        run(*peer, 'ip', '-6', 'addr', 'add', f'fd00:{v6id}::2/64', 'dev', f'c-{name}', 'nodad')
        run(*peer, 'ip', 'link', 'set', f'c-{name}', 'up')
        run(*peer, 'ip', 'route', 'add', 'default', 'via', f'{subnet}.1')
        run(*peer, 'ip', '-6', 'route', 'add', 'default', 'via', f'fd00:{v6id}::1')
    ip('route', 'add', 'default', 'via', '198.18.0.2')
    run('sysctl', '-qw', 'net.ipv4.ip_forward=1', 'net.ipv6.conf.all.forwarding=1')

    # Production classification plus minimal test-only routing/NAT rules.
    nft_text = 'table inet fw4 {\n' + render('selective-cake.nft.j2') + '\n}\n'
    nat_text = '''table ip test_nat {
        chain post { type nat hook postrouting priority srcnat; policy accept;
            oifname "eth1" masquerade
        }
    }
    '''
    run(NFT, '-c', '-f', '-', input=nft_text + nat_text)
    run(NFT, '-f', '-', input=nft_text + nat_text)
    directory = Path(work.name)
    config = directory / 'config'
    config.write_text(render('selective-cake.conf.j2'))
    env = os.environ.copy()
    env['SELECTIVE_CAKE_CONFIG'] = str(config)
    env['SELECTIVE_CAKE_STATE_DIR'] = str(directory / 'state')

    # Probe CAKE. On kernels without it, exercise the exact redirects with TBF.
    ip('link', 'add', 'probe-ifb', 'type', 'ifb')
    probe = subprocess.run([TC, 'qdisc', 'add', 'dev', 'probe-ifb', 'root', 'cake', 'bandwidth', '10mbit'], capture_output=True)
    native_cake = probe.returncode == 0
    ip('link', 'del', 'probe-ifb')
    if not native_cake:
        wrapper = directory / 'tc'
        wrapper.write_text(f'''#!{sys.executable}
import subprocess, sys
args = sys.argv[1:]
if args[:2] == ['qdisc', 'replace'] and 'cake' in args:
    index = args.index('cake')
    rate = args[args.index('bandwidth') + 1]
    args = args[:index] + ['tbf', 'rate', rate, 'burst', '32kb', 'latency', '100ms']
result = subprocess.run([{TC!r}] + args, capture_output=True)
out = result.stdout
if args[:2] == ['qdisc', 'show']:
    out = out.replace(b'qdisc tbf ', b'qdisc cake ')
sys.stdout.buffer.write(out)
sys.stderr.buffer.write(result.stderr)
sys.exit(result.returncode)
''')
        wrapper.chmod(0o755)
        env['PATH'] = str(directory) + ':' + env['PATH']
    helper = str(ROOT / 'roles/cake_autorate/files/selective-cake')

    def shaping(action):
        subprocess.run(['sh', helper, action], env=env, check=True)

    shaping('start')
    shaping('refresh')
    server_code = '''import socket
s=socket.socket(socket.AF_INET6,socket.SOCK_DGRAM)
s.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,0)
s.bind(('::',20001))
while True:
 data,addr=s.recvfrom(4096)
 s.sendto(addr[0].encode()+b'|'+data,addr)
'''
    for peer, _, _ in peers.values():
        servers.append(subprocess.Popen(peer + [sys.executable, '-u', '-c', server_code], stdout=subprocess.DEVNULL))
    time.sleep(.5)

    def transfer(source, target, ipv6=False, count=20):
        peer, _, _ = peers[source]
        destination = peers[target][2 if ipv6 else 1]
        code = '''import socket,sys
s=socket.socket(socket.AF_INET6 if ':' in sys.argv[1] else socket.AF_INET,socket.SOCK_DGRAM)
s.settimeout(3)
addresses=set()
for n in range(int(sys.argv[2])):
 payload=bytes([n%256])*1000
 s.sendto(payload,(sys.argv[1],20001))
 data,_=s.recvfrom(4096)
 observed,body=data.split(b'|',1)
 assert body==payload
 addresses.add(observed.decode())
print(','.join(sorted(addresses)))
'''
        return run(*peer, sys.executable, '-c', code, destination, str(count)).strip()

    def counts():
        result = []
        for device in (VALUES['cake_upload_ifb'], VALUES['cake_download_ifb']):
            stats = json.loads(run(TC, '-s', '-j', 'qdisc', 'show', 'dev', device))
            result.append(sum(q.get('packets', 0) for q in stats if q.get('root')))
        return result

    for source, target, ipv6, expected in [
        ('lan', 'wan', False, (20, 20)),
        ('iot', 'wan', False, (20, 20)),
        ('vpn', 'wan', False, (20, 20)),
        ('bypass', 'wan', False, (0, 0)),
        ('lan', 'iot', False, (0, 0)),
        ('bypass', 'lan', False, (0, 0)),
        ('lan', 'bypass', False, (0, 0)),
        ('lan', 'wan', True, (20, 20)),
        ('iot', 'wan', True, (20, 20)),
    ]:
        # Prime neighbor/conntrack state before checking packet counts.
        transfer(source, target, ipv6, 1)
        time.sleep(.05)
        before = counts()
        observed = transfer(source, target, ipv6)
        time.sleep(.05)
        delta = tuple(after - start for after, start in zip(counts(), before))
        assert delta == expected, (source, target, ipv6, delta, expected)
        if target == 'wan' and not ipv6:
            assert observed.endswith('198.18.0.1'), ('NAT not exercised', observed)
        print(f'PASS {source} -> {target} IPv{6 if ipv6 else 4}: upload/download packets {delta}')

    before = counts()
    # A fw4 reload replaces its table and runs the script include.
    run(NFT, '-f', '-', input='delete table inet fw4\n' + nft_text)
    shaping('refresh')
    assert counts() == before, 'Refreshing must preserve queues and their counters'
    transfer('lan', 'wan')
    delta = tuple(after - start for after, start in zip(counts(), before))
    assert delta == (20, 20), ('firewall reload', delta)
    print('PASS firewall reload: filters restored, queues preserved')

    # A late-created VPN/device is picked up without restarting CAKE.
    ip('link', 'add', 'late-device', 'type', 'dummy')
    shaping('refresh')
    assert 'mirred' in run(TC, 'filter', 'show', 'dev', 'late-device', 'egress')
    print('PASS late interface refresh')
    # Compile the role's forwarding/rule declarations into a minimal nft
    # fixture. fw4 itself still requires validation on OpenWrt.
    tasks = yaml.safe_load((ROOT / 'roles/unrestricted_network/tasks/firewall.yml').read_text())
    forwards = next(t['loop'] for t in tasks if t['name'] == 'Allow requested routed access')
    blocks = next(t['loop'] for t in tasks if t['name'] == 'Explicitly block IoT-initiated access to trusted networks')
    devices = {'lan': 'br-lan.1', 'iot': 'br-lan.20', 'unrestricted': 'br-unrestricted', 'wan': 'eth1'}
    rules = ['ct state established,related accept']
    for dest in blocks:
        rules.append(f'iifname "{devices["iot"]}" oifname "{devices[dest]}" reject')
    rules.append('iifname "br-unrestricted" meta nfproto ipv6 reject')
    for pair in forwards + [{'src': 'lan', 'dest': 'wan'}, {'src': 'iot', 'dest': 'wan'}]:
        rules.append(f'iifname "{devices[pair["src"]]}" oifname "{devices[pair["dest"]]}" accept')
    text = 'table inet access_test { chain forward { type filter hook forward priority filter; policy drop;\n'
    text += ';\n'.join(rules) + ';\n}\n}\n'
    try:
        run(NFT, '-c', '-f', '-', input=text)
    except subprocess.CalledProcessError as error:
        print(error.stderr, file=sys.stderr)
        raise
    run(NFT, '-f', '-', input=text)
    for source, target in [('bypass', 'lan'), ('bypass', 'iot'), ('lan', 'bypass'), ('bypass', 'wan')]:
        transfer(source, target, count=1)
        print(f'PASS access and established replies: {source} -> {target}')
    for source, target, ipv6 in [('iot', 'lan', False), ('iot', 'bypass', False), ('bypass', 'wan', True)]:
        try:
            transfer(source, target, ipv6, count=1)
        except subprocess.CalledProcessError:
            print(f'PASS rejected new connection: {source} -> {target} IPv{6 if ipv6 else 4}')
        else:
            raise AssertionError(('unexpected permitted connection', source, target, ipv6))
    run(NFT, 'delete', 'table', 'inet', 'access_test')

    shaping('stop')
    assert not Path(env['SELECTIVE_CAKE_STATE_DIR'], 'enabled').exists()
    shaping('start')
    transfer('lan', 'wan', count=1)
    assert counts() == [1, 1], counts()
    shaping('stop')
    print('PASS stop/start: queues and redirects recreated')
    print('Queue implementation: ' + ('native CAKE' if native_cake else 'TBF substitute; native CAKE still requires router verification'))
finally:
    for proc in servers + children:
        proc.terminate()
    for proc in servers + children:
        proc.wait()
    work.cleanup()
