Run static tests and playbook syntax checks from the repository root:

```bash
./venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
ansible-playbook site.yml --syntax-check
```

The reachability tests simulate connection failures and remote command failures
with temporary Ansible connection plugins. They verify exclusion across later
plays in normal and check mode without contacting any real OpenWrt devices.
They also run the production reconnect action with an intentionally nonexistent
remote Python interpreter to prevent Python-dependent connection checks from
being reintroduced.

Rollback retry tests use temporary archives and a fake service to check backup
preservation, timer renewal, and refusal to resume with expired timers, invalid
archives, or a stopped worker. They do not change live rollback timers.

Run the isolated packet-path integration test on Linux with `ip`, `tc`, `nft`,
`nsenter`, `unshare`, and the project's Python environment:

```bash
./venv/bin/python tests/network_paths.py
```

The test creates a user/network namespace and refuses to modify the caller's
network namespace. Some sandboxes require permission to create network links.
It creates temporary WAN, LAN, IoT, unrestricted, and simulated VPN peers, uses
the production nft rules and IFB helper, and checks NAT, IPv6, exact queue packet
counts, local/bypass traffic, firewall reload, late devices, and stop/start.
It also tests the access matrix in a minimal nft fixture built from the role’s
forwarding declarations; native fw4/UCI rendering still needs OpenWrt validation.
Child processes and temporary files are cleaned up; virtual links disappear
with their namespaces.

When the kernel lacks CAKE, the test substitutes TBF only for the root rate
limiter. This still tests the real nft/mark/IFB forwarding path and checks for
lost or repeatedly shaped packets. It does not validate CAKE's algorithm or
live cake-autorate adjustments. The simulated VPN interface tests routed VPN
client traffic, not WireGuard encryption or peer handshakes. Physical VLAN
transport, DSA/swconfig behavior, DHCP, Wi-Fi, and roaming require live checks.

In the implementation environment, extracted nftables binaries were used from
`/tmp` because nftables was not installed system-wide. No test packages or
router settings were changed on the live OpenWrt devices.
