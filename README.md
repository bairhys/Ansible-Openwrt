# Ansible OpenWrt

This Ansible playbook provisions an OpenWrt network from factory defaults. It automatically configures:

- Automatic WAN bandwidth management ([CAKE Autorate](https://github.com/lynxthecat/cake-autorate))
- Unrestricted unrestricted Wi-Fi Wi-Fi on VLAN 30, bypassing CAKE while retaining the WAN firewall
- Wi‑Fi with fast client roaming across all OpenWrt devices
- WireGuard server to access LAN devices with easy QR-code client setup
- Tailscale client with subnet routing
- Dynamic DNS via [no-ip.com](https://www.noip.com/) and [FreeDNS (Afraid)](https://freedns.afraid.org/)
- Hostnames
- Timezones
- WAN DNS servers
- Prometheus Exporters

Tested on OpenWrt 25.12 and 24.10.

## Network Diagram

![OpenWrt Network Diagram](network-diagram.svg)

VPN panels show the WireGuard server and optional Tailscale subnet router.
The services panel covers DNS, DHCP, dynamic DNS, Wi-Fi isolation and roaming,
monitoring, diagnostics, and deployment recovery.

The diagram is a generic overview of playbook behavior with three example APs.
It does not show actual device names, counts, addresses, or physical topology.
It shows logical traffic paths: LAN, IoT, and routed VPN internet
traffic use CAKE; unrestricted Wi-Fi bypasses it. Both paths share the existing WAN
firewall and ISP connection. All configured APs carry VLANs 20 and 30 over
their verified uplinks, alongside the native LAN.

## IoT network

The playbook adds a separate IoT network to the router and every configured access point:

- The IoT SSID is configured by `vault_iot_wifi_ssid`; the existing LAN SSID remains separate.
- Default example VLAN: `20`
- Default example IPv4 subnet and gateway: `192.168.20.0/24`, `192.168.20.1`
- DHCP, DNS, and routing are provided by the main OpenWrt router.
- IoT clients can reach the internet, but the router firewall does not forward IoT traffic to the LAN.
- LAN clients can initiate connections to IoT devices. Replies are allowed by the stateful firewall; new IoT-initiated connections to LAN are explicitly rejected.
- The IoT SSID name and password are configured through `vault_iot_wifi_ssid` and `vault_iot_wifi_password` in `group_vars/all/vault.yml`. Add both variables to your encrypted vault before running the playbook; example values are in `vault_example.yml`.
- It shares the existing Wi-Fi radio settings, country, timezone, and 802.11r/k/v roaming configuration. Its SSID, network, and password remain separate.
- At the end of every playbook run, the configured AP radios and all LAN, IoT, and unrestricted SSID interfaces are explicitly enabled and wireless is reloaded. This briefly interrupts Wi-Fi while each AP applies the enabled state.

The role follows the [OpenWrt VLAN/switch configuration guide](https://openwrt.org/docs/guide-user/network/vlan/switch_configuration). On DSA devices, it configures VLAN 1 as the existing untagged LAN, moves the LAN interface to `br-lan.1`, and configures VLAN 20 as tagged on the verified ports listed in `iot_dsa_trunk_ports`, with the local bridge port enabled. Set `iot_vlan_mode: dsa`, list all existing LAN ports in `iot_dsa_native_ports`, and list only physically verified IoT trunk/uplink ports in `iot_dsa_trunk_ports` in `hosts.yml`. On legacy swconfig devices, the role configures VLAN 20 on the switch CPU port and verified physical trunk ports; specify the switch, CPU device/port, unique switch VLAN index, and uplink port list. Choose VLAN IDs that do not conflict with the device's existing WAN configuration. The IoT network tasks fail before changing any host if its mode or required port mappings are missing. Verify that VLAN 20 and `192.168.20.0/24` are otherwise unused.

Every link from the router through access points and switches must carry VLAN 20 tagged, while preserving the existing LAN's untagged/native traffic. Do not configure VLAN 20 on the WAN link.

### Strict client isolation

Strict IoT client isolation is controlled by `iot_strict_client_isolation` under `all.vars` in `hosts.yml` (default: `true`):

```yaml
iot_strict_client_isolation: true
```

When enabled, the playbook enables Wi-Fi client isolation and installs persistent nftables bridge rules on all OpenWrt devices to block peer IPv4/ARP traffic on the IoT subnet and IPv6 forwarding on VLAN 20, while allowing traffic to and from the gateway. This bridge filtering is needed because router firewall rules alone do not see traffic switched directly between clients on the same VLAN. Set the value to `false` to remove the bridge rules and disable Wi-Fi client isolation; clients on the IoT network will then be able to communicate with each other. The router allows IoT DHCP/DNS and IoT-to-WAN forwarding only, and blocks IoT clients from its management interface; manage OpenWrt through the existing LAN instead.

Wi-Fi client isolation covers peers on the same access point; the bridge rules cover clients connected through different access points.

After applying the playbook, verify from clients on different APs that:

1. They obtain an address in `192.168.20.0/24`, with gateway `192.168.20.1`.
2. They can resolve DNS and reach the internet.
3. They cannot reach another IoT Wi-Fi client.
4. They cannot reach a host on the existing LAN.

## Install Ansible and run the playbook

On Windows, use WSL (Ubuntu/Debian) to run Ansible. Quick steps in WSL:

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv
```

Clone this repository and change into its directory.

### Setup Python Virtual Environment

Create and activate a Python virtual environment:

On Windows (WSL), use:
```bash
python3 -m venv venv
source venv/bin/activate
```

Install Ansible and required dependencies in the virtual environment:

```bash
pip install --upgrade pip
pip install -r requirements.txt
ansible-galaxy collection install -r requirements.yml -p collections
```

### Configure Vault

Create a vault password file containing only your password on the first line:

```bash
nano ~/.vaultfile
```

Copy `vault_example.yml` to `group_vars/all/vault.yml` and update the variables:

```bash
cp vault_example.yml group_vars/all/vault.yml

# Encrypt a vault file (first time)
ansible-vault encrypt group_vars/all/vault.yml

# Edit an encrypted vault file
ansible-vault edit group_vars/all/vault.yml

# View vault contents without editing
ansible-vault view group_vars/all/vault.yml
```

### Configure Hosts

Edit `hosts.yml` for your setup. An example is provided. Change the wifi device paths to suit your device using `cat /etc/config/wireless` otherwise might need to factory reset device to find the correct device paths.

### Run the Playbook

Make sure your virtual environment is activated, then run the playbook:

```bash
# Activate the virtual environment (if not already active)
source venv/bin/activate

# Run the playbook
export ANSIBLE_CONFIG=./ansible.cfg
ansible-playbook site.yml
```

To deactivate the virtual environment when finished:

```bash
deactivate
```

## Access Points Setup

By default, OpenWrt devices share the same factory default settings. This is fine for the main router but not ideal for access points. Before connecting access points to your network and running this Ansible playbook, update their basic settings via LuCI.

1. Preparation

- Connect the OpenWrt access point only to your computer via an Ethernet cable.
- Do not connect it to your main router yet to avoid IP conflicts.

2. Disable the firewall and DHCP; set a static LAN IP

- Log in to LuCI (default 192.168.1.1).
- Navigate to Network → Interfaces and click Edit on the LAN interface.
- Under General Setup:
	- Set the IPv4 address to an unused IP in your main router's subnet (e.g., if your main router is 192.168.1.1, use 192.168.1.2).
	- Set the IPv4 gateway to your main router's IP (e.g., 192.168.1.1).
	- Set "Use custom DNS servers" to your main router's IP.
- In DHCP Server → General Setup, check "Ignore interface" to disable DHCP on this device.
- Under the IPv6 Settings tab, disable RA-Service and disable DHCPv6.

Save and apply the changes, then reconnect the device to your main network.

## Unrestricted Wi-Fi

unrestricted Wi-Fi is broadcast on both configured radios of the router and every AP.
Default example settings use VLAN **30**, subnet **192.168.30.0/24**, gateway **192.168.30.1**,
and DHCP addresses **192.168.30.100–249** with 12-hour leases. It initially
provides IPv4 only. Settings live in `group_vars/all/unrestricted.yml`.

VLAN 30 follows the verified IoT trunk ports on each device, preserving native
LAN and VLAN 20. Intermediate switches must carry VLAN 30 tagged. Legacy
swconfig devices use switch VLAN index 4 (distinct from IoT index 3 and WAN
index 2). The new bridge `br-unrestricted` joins both Wi-Fi bands to that VLAN;
APs provide neither DHCP nor routing on it.

| Initiator | WAN | LAN | IoT | Unrestricted | Router management |
|---|---|---|---|---|---|
| Unrestricted | Yes, bypasses CAKE | Yes | Yes | Yes | No; DHCP/DNS only |
| LAN | Yes, shaped | Existing behavior | Yes | Yes | Existing behavior |
| IoT | Yes, shaped | No | Existing isolation setting | No | No; DHCP/DNS only |

Replies to permitted connections are allowed. Routed access does not relay
broadcast discovery such as mDNS between subnets. Wi-Fi client isolation is
disabled on Unrestricted. Its credentials and roaming mobility domain are separate
from LAN and IoT.

### Deploy on the existing network

First add a dedicated 8–63 character password to the encrypted vault:

```bash
./venv/bin/ansible-vault edit group_vars/all/vault.yml
# Add: vault_unrestricted_wifi_password: "your dedicated password"
```

Then validate and deploy the full configuration, including the unrestricted network:

```bash
./venv/bin/ansible-playbook site.yml --syntax-check
./venv/bin/ansible-playbook site.yml
```

The playbook first checks authenticated SSH access to every selected OpenWrt
host, using a read-only command that requires no remote Python. Inaccessible
hosts are reported and excluded from every subsequent play in that run; other
hosts continue. Exclusion does not change inventory, and the next run checks
all selected hosts again. The SSH probe timeout defaults to 10 seconds and can
be set with `openwrt_reachability_timeout`.

Management verification and post-reboot waits use the local
`openwrt_wait_for_connection` action, which retries a shell command over SSH
without requiring Python on OpenWrt.

The main playbook validates credentials and VLAN settings on accessible
hosts before any changes. It configures the unrestricted network on the router
first and then APs one at a time, as part of full provisioning.
Do not skip the preflight/backup tasks with `--start-at-task`.

Before changing each device, the playbook saves a root-only archive at
`/root/network-backup-<timestamp>.tar.gz` and arms a persistent 30-minute
rollback service. It cancels that timer after management, network, radio,
SSID, and (on the router) firewall/CAKE checks pass. Failed deployments leave
the timer armed; it restores the saved configuration even after a reboot.
Backups remain on the devices. A retry while rollback is still armed reuses
the original pre-change archive and renews its timer for thirty minutes; it
does not overwrite the backup with partially applied configuration. The retry
requires a readable, valid archive and a running rollback service, with more
than 60 seconds left. If rollback is due or expired, let restoration finish
before retrying. Missing/corrupt backups or a stopped rollback worker require
recovery before deployment can resume. Software packages are not reverted by
the configuration rollback. Do not run two deployments concurrently.

After deployment, verify from clients on every AP:

1. Unrestricted receives a `.30` DHCP lease, resolves DNS, and reaches the internet.
2. Unrestricted can initiate to LAN and IoT; LAN can initiate to Unrestricted.
3. IoT cannot initiate to LAN or Unrestricted; replies to Unrestricted requests work.
4. During separate uploads and downloads, LAN/IoT/VPN traffic increments the
   corresponding CAKE queue; Unrestricted transfers do not. Local transfers must
   not increment either queue. Use `tc -s qdisc show dev ifb-cake-ul` and
   `tc -s qdisc show dev ifb-cake-dl`, allowing for background protected traffic.
5. Autorate changes both queue bandwidths. Repeat after a firewall reload,
   network restart, reboot, and second playbook run; verify client roaming.

Both paths still share the ISP connection: unrestricted traffic can saturate
it and increase latency for the shaped networks.

## CAKE Autorate

SQM now uses a custom `selective_cake.qos` script with shared root CAKE queues
on `ifb-cake-ul` (upload) and `ifb-cake-dl` (download). The physical WAN interface is configured for the target device. The playbook stops the previous SQM instance before
replacing its configuration, removing the old global WAN and download IFB
shapers. SQM's configuration and the custom helpers are tracked in the role;
the old static `roles/cake_autorate/files/sqm` is no longer deployed.

The fw4 include marks protected forwarded uploads before source NAT and
protected downloads after destination NAT. Packets entering or leaving
`br-unrestricted` bypass both queues. Egress `tc` filters send marked packets
to the appropriate IFB and clear only the reserved mark bits, preventing
repeated shaping on bridge lower devices. Local forwarded traffic is unmarked.
Router-originated WAN uploads are also shaped; downloads terminating on the
router itself do not pass through the forwarded-download queue.

Reserved skb mark mask: `0x03000000` (upload `0x01000000`, download `0x02000000`).
Other mark bits are preserved. Filter preferences 30300 and 30301 are reserved
for this role. Both software and hardware flow offloading remain disabled.

Hotplug hooks refresh filters for new VLAN, Wi-Fi, WAN, and VPN devices.
The WAN netdevice is resolved using ubus with `cake_wan_device` as fallback.
A firewall script include refreshes the WAN-device set after fw4 reloads.
Queue bandwidths are preserved during refreshes so autorate is not reset.

A custom configuration can still be supplied through `custom_cake_config`
(e.g. `custom_files/config.primary.sh`). The role preserves its rate and other
settings but renders `ul_if=ifb-cake-ul` and `dl_if=ifb-cake-dl` into the deployed
file. The initial queues start at 10 Mbit/s upload and 80 Mbit/s download;
autorate then manages them using the existing custom minimum/base/maximum rates.

The CAKE autorate Prometheus exporter reads its existing primary log and has
no fixed queue interface names. Per-device dashboards should use
`ifb-cake-ul`/`ifb-cake-dl` for shaped traffic and `eth1` for total WAN traffic,
which includes Unrestricted. See `tests/README.md` for isolated validation.

## Tailscale

Tailscale is disabled by default. To enable it, set `tailscale_enabled: true` in your inventory variables.

This Ansible playbook cannot fully automate the security approvals required by the Tailscale platform itself and some manual steps are required:
- Approve Subnet Routes: Go to the Tailscale Admin Console, click the three-dot menu (...) 
  for your OpenWrt router, select Edit route settings, and check the box for your subnet.
- Client Configuration: On your remote device (phone/laptop), ensure the Tailscale app setting 
  "Use Tailscale Subnets" is toggled ON to reach your LAN devices.

## Wireguard tips

- Setup a Dynamic DNS service
- Change port from the default 51820 to a different high a random port. A different port can be defined in the vault.
- To add the Wireguard client to your device with a QR code:
	- On the main router, navigate to Network → Interfaces and click Edit on the Wireguard interface.
	- With the Wireguard interface settings open, navigate to Peers tab, click Edit for client1
	- At the bottom, click Configuration Export
	- Set the Connection Enpoint to your Dynamic DNS service
	- In the Wireguard app on your device, press add and scan QR code
