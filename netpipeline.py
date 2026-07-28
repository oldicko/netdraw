#!/usr/bin/env python3
import argparse
import csv
import ipaddress
import json
import os
import subprocess
import sys
from collections import defaultdict

def ip_to_int(ip):
    try:
        parts = [int(p) for p in ip.strip().split('.')]
        if len(parts) == 4 and all(0 <= p <= 255 for p in parts):
            return (parts[0] << 24) + (parts[1] << 16) + (parts[2] << 8) + parts[3]
    except Exception:
        pass
    return None

def is_rfc1918(ip):
    ip_val = ip_to_int(ip)
    if ip_val is None:
        return False
    # 10.0.0.0/8
    if (ip_val & 0xFF000000) == 0x0A000000:
        return True
    # 172.16.0.0/12
    if (ip_val & 0xFFF00000) == 0xAC100000:
        return True
    # 192.168.0.0/16
    if (ip_val & 0xFFFF0000) == 0xC0A80000:
        return True
    return False

def extract_first_ip(ip_field):
    """Extract the first usable IP from a field that may contain ranges or semicolons."""
    if not ip_field:
        return ""
    ip_field = ip_field.strip()
    # Handle semicolon-separated multi-IPs: "192.168.2.10;172.16.1.99" -> "192.168.2.10"
    if ";" in ip_field:
        ip_field = ip_field.split(";")[0].strip()
    # Handle IP ranges: "10.10.1.50-10.10.1.54" -> "10.10.1.50"
    if "-" in ip_field:
        ip_field = ip_field.split("-")[0].strip()
    return ip_field

def clean_hostname(name):
    if not name:
        return ""
    if "<" in name:
        name = name.split("<")[0]
    return name.strip()

def load_vlans(vlans_path):
    vlan_networks = []
    if not vlans_path or not os.path.exists(vlans_path):
        return vlan_networks
    try:
        with open(vlans_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            headers = [h.strip() for h in reader.fieldnames]
            reader.fieldnames = headers
            cidr_col = "IP Range" if "IP Range" in headers else ("CIDR" if "CIDR" in headers else None)
            if "VLAN ID" not in headers or not cidr_col:
                print(f"Error: VLANs CSV must contain 'VLAN ID' and 'IP Range' (or 'CIDR') in headers: {headers}", file=sys.stderr)
                sys.exit(1)
            for row in reader:
                row = {k.strip(): (v.strip() if v else "") for k, v in row.items() if k}
                vid = row.get("VLAN ID", "").strip()
                desc = row.get("Description", "").strip()
                cidr = row.get(cidr_col, "").strip()
                zone = row.get("Zone", "").strip()
                if not vid:
                    vid = desc if desc else cidr
                if vid and cidr:
                    try:
                        net = ipaddress.ip_network(cidr, strict=False)
                        vlan_networks.append({
                            "vlan_id": vid,
                            "network": net,
                            "zone": zone
                        })
                    except Exception as ne:
                        print(f"Warning: Invalid CIDR '{cidr}' in VLAN {vid}: {ne}", file=sys.stderr)
    except Exception as e:
        print(f"Error reading VLANs CSV: {e}", file=sys.stderr)
        sys.exit(1)
    return vlan_networks

def get_vlan_info(ip, vlan_networks):
    try:
        ip_obj = ipaddress.ip_address(ip)
        for vn in vlan_networks:
            if ip_obj in vn["network"]:
                return vn["vlan_id"], vn["zone"]
    except Exception:
        pass
    return None, None

def is_internal_ip(ip, vlan_networks, config_networks):
    if not ip:
        return False
    # 1. Matches VLAN subnet?
    vlan_id, _ = get_vlan_info(ip, vlan_networks)
    if vlan_id is not None:
        return True
    # 2. Is RFC-1918?
    if is_rfc1918(ip):
        return True
    # 3. Matches config CIDR ranges?
    try:
        ip_obj = ipaddress.ip_address(ip)
        for net in config_networks:
            if ip_obj in net:
                return True
    except Exception:
        pass
    return False

def is_wan_ip(ip, vlan_networks, config_networks):
    # Any non-internal IP is WAN
    if not ip or ip == "WAN":
        return True
    if is_internal_ip(ip, vlan_networks, config_networks):
        return False
    try:
        ip_obj = ipaddress.ip_address(ip)
        if ip_obj.is_multicast or ip_obj.is_loopback or ip_obj.is_link_local:
            return False
    except Exception:
        return False
    return True

def is_multicast_or_broadcast(ip, vlan_networks):
    if not ip:
        return True
    if ip == "255.255.255.255" or ip == "0.0.0.0":
        return True
    try:
        ip_obj = ipaddress.ip_address(ip)
        if ip_obj.is_multicast:
            return True
        for vn in vlan_networks:
            if ip_obj == vn["network"].broadcast_address or ip_obj == vn["network"].network_address:
                return True
    except Exception:
        pass
    return False

def load_protocols(protocols_path):
    protocols_dict = {}
    if not os.path.exists(protocols_path):
        return protocols_dict
    try:
        with open(protocols_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            headers = [h.strip() for h in reader.fieldnames]
            reader.fieldnames = headers
            if "Protocol" not in headers or "Port" not in headers or "Name" not in headers:
                return protocols_dict
            for row in reader:
                row = {k.strip(): v.strip() for k, v in row.items()}
                proto = row["Protocol"].lower()
                port = int(row["Port"])
                name = row["Name"]
                protocols_dict[(proto, port)] = name
    except Exception as e:
        print(f"Warning: Failed to load protocols.csv: {e}", file=sys.stderr)
    return protocols_dict

def get_service_port_and_direction(proto, srcport, dstport, protocols_dict):
    src_registered = (proto, srcport) in protocols_dict
    dst_registered = (proto, dstport) in protocols_dict
    
    if dst_registered and not src_registered:
        return dstport, True
    if src_registered and not dst_registered:
        return srcport, False
        
    if dstport < 1024 and srcport >= 1024:
        return dstport, True
    if srcport < 1024 and dstport >= 1024:
        return srcport, False
        
    return dstport, True

def should_ignore_flow(proto, port, protocol_name, ignore_protocols, ignore_ports):
    if protocol_name and any(p.lower() == protocol_name.lower() for p in ignore_protocols):
        return True
    port_str = f"{proto.lower()}:{port}"
    if any(p.lower() == port_str for p in ignore_ports):
        return True
    return False

def read_csv_file(path):
    rows = []
    if not os.path.exists(path):
        return rows
    try:
        with open(path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append({k.strip(): v.strip() for k, v in row.items() if k is not None})
            return rows
    except UnicodeDecodeError:
        pass
    try:
        with open(path, mode="r", encoding="cp1252", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append({k.strip(): v.strip() for k, v in row.items() if k is not None})
            return rows
    except Exception as e:
        print(f"Warning: Could not read file {path}: {e}", file=sys.stderr)
    return rows

def prompt_conflict(entity_type, identifier, orig_desc, disc_desc):
    print(f"\n[CONFLICT] Duplicate {entity_type} '{identifier}' has conflicting values!")
    print(f"  Existing/Original: {orig_desc}")
    print(f"  Newly Discovered:  {disc_desc}")
    while True:
        choice = input("Resolve conflict: (1) Keep original, (2) Overwrite, (3) Keep both (append): ").strip()
        if choice in ("1", "2", "3"):
            return choice
        print("Invalid choice. Please enter 1, 2, or 3.")

def get_next_available_letter(used_ids, assigned_map):
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    index = 0
    while True:
        temp = ""
        n = index
        while True:
            temp = chars[n % 26] + temp
            n = (n // 26) - 1
            if n < 0:
                break
        if temp not in used_ids and temp not in assigned_map.values():
            return temp
        index += 1

def get_unique_vlan_id(vid, assigned_vlan_ids):
    if vid not in assigned_vlan_ids:
        return vid
    try:
        int(vid)
        suffix = 2
        while f"{vid}-{suffix}" in assigned_vlan_ids:
            suffix += 1
        return f"{vid}-{suffix}"
    except ValueError:
        return get_next_available_letter(assigned_vlan_ids, {})

def resolve_vlan_id(ip, subnet_to_vlan):
    """Resolve a VLAN ID for an IP by checking all known subnets, then falling back to /24."""
    if not ip:
        return ""
    try:
        ip_obj = ipaddress.ip_address(ip)
        # Check all mapped subnets (supports /25, /26, etc.)
        for net_str, vid in subnet_to_vlan.items():
            if ip_obj in ipaddress.ip_network(net_str, strict=False):
                return vid
    except Exception:
        pass
    # Fallback: try /24
    try:
        net = ipaddress.ip_network(f"{ip}/24", strict=False)
        return subnet_to_vlan.get(str(net), "")
    except Exception:
        return ""

def main():
    parser = argparse.ArgumentParser(description="OT/IT Network Pipeline Utility.")
    parser.add_argument("-i", "--input-assets", required=False, default=None, help="Path to input assets CSV file (starting list of OT assets, optional)")
    parser.add_argument("-p", "--pcap", required=True, help="Path to pcapng or gzipped pcapng file")
    parser.add_argument("-v", "--vlans", default="vlans.csv", help="Path to VLANs CSV file (defaults to vlans.csv)")
    parser.add_argument("-c", "--config", default="config.json", help="Path to config.json file")
    parser.add_argument("-a", "--output-assets", default="discovered_assets.csv", help="Path to output assets CSV file")
    parser.add_argument("-f", "--output-flows", default="discovered_flows.csv", help="Path to output flows CSV file")
    parser.add_argument("-e", "--external", default="external.csv", help="Path to output external WAN log CSV file")
    parser.add_argument("--append", action="store_true", help="Append to existing files and resolve conflicts")
    parser.add_argument("--protocols", default="protocols.csv", help="Path to protocols lookup CSV")
    parser.add_argument("--high-port-min", type=int, default=None, help="Minimum port threshold for high-port range grouping (defaults to 49152)")
    parser.add_argument("--high-port-threshold", type=int, default=None, help="Minimum count of high ports to trigger range grouping (defaults to 50)")
    parser.add_argument("--high-port-gap", type=int, default=None, help="Maximum port gap between high ports in same range cluster (defaults to 1000)")
    
    args = parser.parse_args()

    # Validate input files exist
    if args.input_assets and not os.path.exists(args.input_assets):
        print(f"Error: Input assets file not found: {args.input_assets}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(args.pcap):
        print(f"Error: PCAP file not found: {args.pcap}", file=sys.stderr)
        sys.exit(1)

    # Verify tshark installation
    try:
        subprocess.run(["tshark", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except Exception:
        print("Error: 'tshark' is not installed or not available in PATH.", file=sys.stderr)
        print("       Install Wireshark/tshark: https://www.wireshark.org/download.html", file=sys.stderr)
        sys.exit(1)

    print("netpipeline: Loading configuration...")
    vlan_networks = load_vlans(args.vlans)
    protocols_dict = load_protocols(args.protocols)

    # Load starting OT assets (if provided)
    input_assets = []
    ot_asset_ips = set()
    if args.input_assets and os.path.exists(args.input_assets):
        input_assets = read_csv_file(args.input_assets)
        for row in input_assets:
            ip_field = row.get("IP address", "").strip()
            if not ip_field:
                continue
            for ip_part in ip_field.split(";"):
                ip_part = ip_part.strip()
                if "-" in ip_part:
                    range_parts = ip_part.split("-")
                    ot_asset_ips.add(range_parts[0].strip())
                    if len(range_parts) > 1:
                        ot_asset_ips.add(range_parts[1].strip())
                elif ip_part:
                    ot_asset_ips.add(ip_part)
        print(f"netpipeline: Loaded {len(input_assets)} starting OT asset(s) ({len(ot_asset_ips)} unique IP(s)).")
    else:
        print("netpipeline: No starting asset list provided. All discovered internal assets will be considered in-scope.")

    # Load configuration
    config_data = {}
    ignore_protocols = []
    ignore_ports = []
    config_networks = []
    high_port_min = 49152
    high_port_threshold = 50
    high_port_gap = 1000
    
    if os.path.exists(args.config):
        try:
            with open(args.config, mode="r", encoding="utf-8") as f:
                config_data = json.load(f)
            pipeline_cfg = config_data.get("pipeline", {})
            ignore_protocols = pipeline_cfg.get("ignore_protocols", [])
            ignore_ports = pipeline_cfg.get("ignore_ports", [])
            high_port_min = pipeline_cfg.get("high_port_min", 49152)
            high_port_threshold = pipeline_cfg.get("high_port_threshold", 50)
            high_port_gap = pipeline_cfg.get("high_port_gap", 1000)
            for cidr in pipeline_cfg.get("cidr_ranges", []):
                try:
                    config_networks.append(ipaddress.ip_network(cidr, strict=False))
                except Exception as e:
                    print(f"Warning: Invalid CIDR '{cidr}' in config: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Warning: Failed to load config.json: {e}", file=sys.stderr)

    if args.high_port_min is not None:
        high_port_min = args.high_port_min
    if args.high_port_threshold is not None:
        high_port_threshold = args.high_port_threshold
    if args.high_port_gap is not None:
        high_port_gap = args.high_port_gap

    # Passive discovery structures
    ip_hostnames = defaultdict(list)
    mac_source_ips = defaultdict(set)
    ip_source_macs = defaultdict(set)
    arp_ip_macs = defaultdict(set)
    ip_mac_packet_count = defaultdict(int)
    ip_mac_vlans = defaultdict(list)
    
    # TCP & UDP flow packet tracking
    tcp_conversations = {}
    tcp_conn_pkt_counts = defaultdict(int)
    tcp_conn_info = {}
    udp_flow_pkt_counts = defaultdict(int)

    # Build tshark command
    tshark_cmd = [
        "tshark", "-r", args.pcap,
        "-T", "fields",
        "-e", "ip.src", "-e", "eth.src",
        "-e", "ip.dst", "-e", "eth.dst",
        "-e", "ip.ttl",
        "-e", "arp.src.proto_ipv4", "-e", "arp.src.hw_mac",
        "-e", "dns.resp.name", "-e", "dns.a",
        "-e", "dhcp.option.hostname",
        "-e", "bootp.option.hostname",
        "-e", "bootp.ip.your",
        "-e", "nbns.name",
        "-e", "tls.handshake.extensions_server_name", "-e", "http.host",
        "-e", "tcp.dstport", "-e", "udp.dstport",
        "-e", "tcp.srcport", "-e", "udp.srcport",
        "-e", "tcp.flags",
        "-e", "vlan.id",
        "-E", "separator=;"
    ]

    try:
        proc = subprocess.Popen(tshark_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except Exception as e:
        print(f"Error starting tshark: {e}", file=sys.stderr)
        sys.exit(1)

    def get_first(val):
        val = val.strip()
        if not val:
            return ""
        return val.split(',')[0].strip()

    packet_count = 0
    print(f"netpipeline: Running tshark on '{args.pcap}'...")
    
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        
        parts = line.split(';')
        if len(parts) < 20:
            continue
            
        packet_count += 1
        if packet_count % 10000 == 0:
            print(f"  Processed {packet_count} packets...", flush=True)

        ip_src = get_first(parts[0])
        eth_src = get_first(parts[1])
        ip_dst = get_first(parts[2])
        eth_dst = get_first(parts[3])
        ip_ttl = get_first(parts[4])
        arp_ip = get_first(parts[5])
        arp_mac = get_first(parts[6])
        dns_name = get_first(parts[7])
        dns_ip = get_first(parts[8])
        dhcp_host = get_first(parts[9])
        bootp_host = get_first(parts[10])
        bootp_your_ip = get_first(parts[11])
        nbns_name = get_first(parts[12])
        tls_sni = get_first(parts[13])
        http_host = get_first(parts[14])
        tcp_dstport = get_first(parts[15])
        udp_dstport = get_first(parts[16])
        tcp_srcport = get_first(parts[17])
        udp_srcport = get_first(parts[18])
        tcp_flags = get_first(parts[19]) if len(parts) > 19 else ""
        vlan_id_pkt = get_first(parts[20]) if len(parts) > 20 else ""

        # Passive Hostname / MAC tracking
        if dns_name and dns_ip:
            ip_hostnames[dns_ip].append(clean_hostname(dns_name))
        if dhcp_host and bootp_your_ip:
            ip_hostnames[bootp_your_ip].append(clean_hostname(dhcp_host))
        elif bootp_host and bootp_your_ip:
            ip_hostnames[bootp_your_ip].append(clean_hostname(bootp_host))
        if tls_sni and ip_dst:
            ip_hostnames[ip_dst].append(clean_hostname(tls_sni))
        elif http_host and ip_dst:
            ip_hostnames[ip_dst].append(clean_hostname(http_host))
        if nbns_name and ip_src:
            ip_hostnames[ip_src].append(clean_hostname(nbns_name))
            
        if arp_ip and arp_mac:
            arp_ip_macs[arp_ip].add(arp_mac)

        if vlan_id_pkt:
            if ip_src and eth_src:
                ip_mac_vlans[(ip_src, eth_src)].append(vlan_id_pkt)
            if ip_dst and eth_dst:
                ip_mac_vlans[(ip_dst, eth_dst)].append(vlan_id_pkt)

        if ip_src and eth_src:
            src_vlan, _ = get_vlan_info(ip_src, vlan_networks)
            mac_source_ips[eth_src].add(ip_src)
            is_local = False
            dst_vlan, _ = get_vlan_info(ip_dst, vlan_networks) if ip_dst else (None, None)
            if src_vlan is not None and src_vlan == dst_vlan:
                is_local = True
            elif ip_ttl:
                try:
                    ttl_val = int(ip_ttl)
                    if ttl_val in (64, 128, 255):
                        is_local = True
                except ValueError:
                    pass
            if is_local:
                ip_source_macs[ip_src].add(eth_src)
                ip_mac_packet_count[(ip_src, eth_src)] += 1

        # Flows extraction
        if ip_src and ip_dst:
            if is_multicast_or_broadcast(ip_src, vlan_networks) or is_multicast_or_broadcast(ip_dst, vlan_networks):
                continue
                
            if tcp_dstport or tcp_srcport:
                proto = "tcp"
                sport = int(tcp_srcport) if tcp_srcport else 0
                dport = int(tcp_dstport) if tcp_dstport else 0
                if sport or dport:
                    endpoint1 = (ip_src, sport)
                    endpoint2 = (ip_dst, dport)
                    conn_key = (endpoint1, endpoint2) if endpoint1 < endpoint2 else (endpoint2, endpoint1)
                    
                    tcp_conn_pkt_counts[conn_key] += 1
                    
                    is_syn = False
                    if tcp_flags:
                        try:
                            val = int(tcp_flags, 16) if tcp_flags.lower().startswith('0x') else int(tcp_flags)
                            if (val & 0x02) and not (val & 0x10):
                                is_syn = True
                        except ValueError:
                            pass
                    
                    if conn_key not in tcp_conversations:
                        tcp_conversations[conn_key] = False
                    if is_syn:
                        tcp_conversations[conn_key] = True
                        tcp_conn_info[conn_key] = (ip_src, ip_dst, "tcp", dport)
            elif udp_dstport or udp_srcport:
                proto = "udp"
                sport = int(udp_srcport) if udp_srcport else 0
                dport = int(udp_dstport) if udp_dstport else 0
                if sport or dport:
                    port, is_dst_service = get_service_port_and_direction(proto, sport, dport, protocols_dict)
                    if is_dst_service:
                        client_ip = ip_src
                        server_ip = ip_dst
                    else:
                        client_ip = ip_dst
                        server_ip = ip_src
                    udp_flow_key = (client_ip, server_ip, "udp", port)
                    udp_flow_pkt_counts[udp_flow_key] += 1

    proc.stdout.close()
    proc.wait()
    print(f"netpipeline: Passive analysis complete. Processed {packet_count} total packets.")

    # Gateway MAC resolution
    gateway_macs = set()
    for mac, ips in mac_source_ips.items():
        subnets_seen = set()
        for ip in ips:
            if ip and ip != "WAN":
                try:
                    net = ipaddress.ip_network(f"{ip}/24", strict=False)
                    subnets_seen.add(str(net))
                except Exception:
                    pass
        if len(subnets_seen) > 1:
            gateway_macs.add(mac)

    # DHCP IP update check
    mac_to_active_ip = {}
    mac_ip_counts = defaultdict(int)
    for (ip, mac), count in ip_mac_packet_count.items():
        if mac not in gateway_macs and not is_multicast_or_broadcast(ip, vlan_networks):
            mac_ip_counts[(mac, ip)] += count

    for ip, macs in arp_ip_macs.items():
        for mac in macs:
            if mac not in gateway_macs and not is_multicast_or_broadcast(ip, vlan_networks):
                mac_ip_counts[(mac, ip)] += 1

    mac_to_ips = defaultdict(list)
    for (mac, ip), count in mac_ip_counts.items():
        mac_to_ips[mac].append((ip, count))

    for mac, ip_list in mac_to_ips.items():
        ip_list.sort(key=lambda x: x[1], reverse=True)
        mac_to_active_ip[mac] = ip_list[0][0]

    updated_ips_count = 0
    for row in input_assets:
        orig_mac = row.get("MAC address", "").strip()
        if orig_mac and orig_mac in mac_to_active_ip:
            active_ip = mac_to_active_ip[orig_mac]
            orig_ip_field = row.get("IP address", "").strip()
            # Check if the active IP is genuinely new (not already present in the field)
            orig_ips = {p.strip() for p in orig_ip_field.replace("-", ";").split(";") if p.strip()}
            if orig_ip_field and active_ip not in orig_ips:
                print(f"netpipeline: Asset '{row.get('Hostname')}' (MAC {orig_mac}) changed IP from '{orig_ip_field}' to '{active_ip}' (DHCP update).")
                row["IP address"] = active_ip
                updated_ips_count += 1

    if updated_ips_count > 0:
        # Re-populate ot_asset_ips with updated IP addresses (same parsing as initial load)
        ot_asset_ips = set()
        for row in input_assets:
            ip_field = row.get("IP address", "").strip()
            if not ip_field:
                continue
            for ip_part in ip_field.split(";"):
                ip_part = ip_part.strip()
                if "-" in ip_part:
                    range_parts = ip_part.split("-")
                    ot_asset_ips.add(range_parts[0].strip())
                    if len(range_parts) > 1:
                        ot_asset_ips.add(range_parts[1].strip())
                elif ip_part:
                    ot_asset_ips.add(ip_part)

    # 1. Release TCP flows for conversations that had a SYN handshake, and include UDP flows
    candidate_flows_map = defaultdict(int)
    
    for conn_key, is_syn_seen in tcp_conversations.items():
        if is_syn_seen and conn_key in tcp_conn_info:
            client_ip, server_ip, proto, port = tcp_conn_info[conn_key]
            protocol_name = protocols_dict.get((proto, port), "")
            if should_ignore_flow(proto, port, protocol_name, ignore_protocols, ignore_ports):
                continue
            cnt = tcp_conn_pkt_counts[conn_key]
            candidate_flows_map[(client_ip, server_ip, proto, port)] += cnt
            
    for (client_ip, server_ip, proto, port), cnt in udp_flow_pkt_counts.items():
        protocol_name = protocols_dict.get((proto, port), "")
        if should_ignore_flow(proto, port, protocol_name, ignore_protocols, ignore_ports):
            continue
        candidate_flows_map[(client_ip, server_ip, proto, port)] += cnt

    # 2. Build direct adjacency list of internal flows for Transitive Discovery
    # We also keep track of all candidates (internal and external)
    internal_adj = defaultdict(set)
    all_flows_by_endpoints = defaultdict(lambda: defaultdict(int))
    
    for (client, server, proto, port), cnt in candidate_flows_map.items():
        client_wan = is_wan_ip(client, vlan_networks, config_networks)
        server_wan = is_wan_ip(server, vlan_networks, config_networks)
        
        # We only look at connections where neither endpoint is multicast/broadcast
        if is_multicast_or_broadcast(client, vlan_networks) or is_multicast_or_broadcast(server, vlan_networks):
            continue
            
        all_flows_by_endpoints[(client, server)][(proto, port)] += cnt
        
        if not client_wan and not server_wan:
            internal_adj[client].add(server)
            internal_adj[server].add(client)

    # 3. In-scope Asset Determination
    active_ot_ips = set(ot_asset_ips)
    if ot_asset_ips:
        queue = list(ot_asset_ips)
        visited = set(ot_asset_ips)
        
        while queue:
            current_ip = queue.pop(0)
            for neighbor in internal_adj[current_ip]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    active_ot_ips.add(neighbor)
                    queue.append(neighbor)

        print(f"netpipeline: Discovered {len(active_ot_ips) - len(ot_asset_ips)} OT-related asset(s) transitively.")
    else:
        # No starting asset list provided: treat all internal (non-WAN) assets observed in capture as in-scope
        all_internal_ips = set()
        for client, server in all_flows_by_endpoints.keys():
            if not is_wan_ip(client, vlan_networks, config_networks) and not is_multicast_or_broadcast(client, vlan_networks):
                all_internal_ips.add(client)
            if not is_wan_ip(server, vlan_networks, config_networks) and not is_multicast_or_broadcast(server, vlan_networks):
                all_internal_ips.add(server)
        for ip_dict in (ip_source_macs, arp_ip_macs, ip_hostnames):
            for ip in ip_dict.keys():
                if ip and not is_wan_ip(ip, vlan_networks, config_networks) and not is_multicast_or_broadcast(ip, vlan_networks):
                    all_internal_ips.add(ip)
        active_ot_ips = all_internal_ips
        print(f"netpipeline: No starting asset list provided. Discovered {len(active_ot_ips)} in-scope internal asset(s) from capture.")

    # 4. Filter flows and apply High-Port Range Grouping: only keep if at least one endpoint is in active_ot_ips
    filtered_flows = []
    external_flows_log = set()
    
    for (client, server), proto_port_map in all_flows_by_endpoints.items():
        client_in_ot = client in active_ot_ips
        server_in_ot = server in active_ot_ips
        
        if not client_in_ot and not server_in_ot:
            continue
            
        client_wan = is_wan_ip(client, vlan_networks, config_networks)
        server_wan = is_wan_ip(server, vlan_networks, config_networks)
        
        # Exclude WAN-to-WAN flows (should not happen, but check)
        if client_wan and server_wan:
            continue
            
        src_ref = "WAN" if client_wan else client
        dst_ref = "WAN" if server_wan else server
        
        by_proto = defaultdict(dict)
        for (proto, port), cnt in proto_port_map.items():
            by_proto[proto][port] = cnt
            
            # Log external connection if one endpoint is WAN
            if client_wan and not server_wan:
                resolved_host = max(set(ip_hostnames[client]), key=ip_hostnames[client].count) if ip_hostnames[client] else ""
                external_flows_log.add((client, resolved_host, proto.upper(), port, server, "inbound"))
            elif server_wan and not client_wan:
                resolved_host = max(set(ip_hostnames[server]), key=ip_hostnames[server].count) if ip_hostnames[server] else ""
                external_flows_log.add((server, resolved_host, proto.upper(), port, client, "outbound"))

        for proto, port_counts in by_proto.items():
            low_ports = {p: c for p, c in port_counts.items() if p < high_port_min}
            high_ports = {p: c for p, c in port_counts.items() if p >= high_port_min}
            
            # Output low ports individually
            for port, cnt in sorted(low_ports.items()):
                proto_name = protocols_dict.get((proto, port), "")
                comment_suffix = f" - {proto_name}" if proto_name else ""
                comment = f"{proto.upper()}{port}{comment_suffix}"
                filtered_flows.append({
                    "src": src_ref,
                    "dst": dst_ref,
                    "comment": comment,
                    "count": cnt
                })
                
            # Handle high ports grouping
            if len(high_ports) <= high_port_threshold:
                for port, cnt in sorted(high_ports.items()):
                    proto_name = protocols_dict.get((proto, port), "")
                    comment_suffix = f" - {proto_name}" if proto_name else ""
                    comment = f"{proto.upper()}{port}{comment_suffix}"
                    filtered_flows.append({
                        "src": src_ref,
                        "dst": dst_ref,
                        "comment": comment,
                        "count": cnt
                    })
            else:
                sorted_ports = sorted(high_ports.keys())
                clusters = []
                curr_cluster = []
                
                for p in sorted_ports:
                    if not curr_cluster:
                        curr_cluster.append(p)
                    else:
                        if p - curr_cluster[-1] <= high_port_gap:
                            curr_cluster.append(p)
                        else:
                            clusters.append(curr_cluster)
                            curr_cluster = [p]
                if curr_cluster:
                    clusters.append(curr_cluster)
                    
                for cluster in clusters:
                    if len(cluster) == 1:
                        port = cluster[0]
                        cnt = high_ports[port]
                        proto_name = protocols_dict.get((proto, port), "")
                        comment_suffix = f" - {proto_name}" if proto_name else ""
                        comment = f"{proto.upper()}{port}{comment_suffix}"
                        filtered_flows.append({
                            "src": src_ref,
                            "dst": dst_ref,
                            "comment": comment,
                            "count": cnt
                        })
                    else:
                        min_p = cluster[0]
                        max_p = cluster[-1]
                        total_cnt = sum(high_ports[p] for p in cluster)
                        comment = f"{proto.upper()}{min_p}-{max_p}"
                        filtered_flows.append({
                            "src": src_ref,
                            "dst": dst_ref,
                            "comment": comment,
                            "count": total_cnt
                        })

    # 5. Build unique subnet-to-VLAN mapping
    subnet_to_vlan = {}
    assigned_vlan_ids = set()

    # 5.1. Load VLANs from loaded vlan_networks
    for vn in vlan_networks:
        if vn["vlan_id"] and vn["network"]:
            final_vid = get_unique_vlan_id(vn["vlan_id"], assigned_vlan_ids)
            subnet_to_vlan[str(vn["network"])] = final_vid
            assigned_vlan_ids.add(final_vid)

    # 5.2. Load VLANs from existing vlans.csv (even if not fully parsed network-wise)
    if os.path.exists(args.vlans):
        try:
            with open(args.vlans, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                headers = [h.strip() for h in (reader.fieldnames or [])]
                cidr_col = "IP Range" if "IP Range" in headers else ("CIDR" if "CIDR" in headers else None)
                if cidr_col:
                    for row in reader:
                        row = {k.strip(): (v.strip() if v else "") for k, v in row.items() if k}
                        vid = row.get("VLAN ID", "").strip()
                        desc = row.get("Description", "").strip()
                        ipr = row.get(cidr_col, "").strip()
                        if not vid:
                            vid = desc if desc else ipr
                        if vid and ipr:
                            try:
                                net = ipaddress.ip_network(ipr, strict=False)
                                net_str = str(net)
                                if net_str not in subnet_to_vlan:
                                    final_vid = get_unique_vlan_id(vid, assigned_vlan_ids)
                                    subnet_to_vlan[net_str] = final_vid
                                    assigned_vlan_ids.add(final_vid)
                            except Exception:
                                pass
        except Exception:
            pass

    # 5.3. Load VLANs manually configured in input starting assets
    for row in input_assets:
        ip = row.get("IP address", "").strip()
        vid = row.get("VLAN ID", "").strip()
        if ip and vid:
            try:
                net = ipaddress.ip_network(f"{ip}/24", strict=False)
                net_str = str(net)
                if net_str not in subnet_to_vlan:
                    final_vid = get_unique_vlan_id(vid, assigned_vlan_ids)
                    subnet_to_vlan[net_str] = final_vid
                    assigned_vlan_ids.add(final_vid)
            except Exception:
                pass

    # 5.4. Discover and assign unique VLAN IDs to all active subnets
    active_subnets = set()
    for ip in active_ot_ips:
        if not is_wan_ip(ip, vlan_networks, config_networks):
            try:
                net = ipaddress.ip_network(f"{ip}/24", strict=False)
                active_subnets.add(str(net))
            except Exception:
                pass

    for subnet_str in sorted(active_subnets):
        if subnet_str in subnet_to_vlan:
            continue

        final_vid = get_next_available_letter(assigned_vlan_ids, {})
        subnet_to_vlan[subnet_str] = final_vid
        assigned_vlan_ids.add(final_vid)

    # 5.5. Populate asset details for all active assets
    # We must preserve the starting OT assets exactly (including duplicates and blanks) but populate any of their blank fields.
    discovered_details = {}
    for ip in active_ot_ips:
        # Resolve MAC if blank
        resolved_mac = ""
        valid_macs = [mac for mac in ip_source_macs[ip] if mac not in gateway_macs]
        if not valid_macs and arp_ip_macs[ip]:
            valid_macs = [mac for mac in arp_ip_macs[ip] if mac not in gateway_macs]
        if valid_macs:
            resolved_mac = max(valid_macs, key=lambda m: ip_mac_packet_count.get((ip, m), 0))
        
        # Resolve Hostname if blank
        resolved_host = ""
        if ip_hostnames[ip]:
            resolved_host = max(set(ip_hostnames[ip]), key=ip_hostnames[ip].count)
        else:
            resolved_host = ip
            
        # Resolve VLAN ID from our unique subnet_to_vlan map
        vlan_id = resolve_vlan_id(ip, subnet_to_vlan)
                
        discovered_details[ip] = {
            "Hostname": resolved_host,
            "MAC address": resolved_mac,
            "VLAN ID": vlan_id
        }

    final_discovered_assets = []
    for row in input_assets:
        new_row = dict(row)
        ip_field = row.get("IP address", "").strip()
        # Try to match via first usable IP for range/multi-IP assets
        first_ip = extract_first_ip(ip_field)
        matched_details = discovered_details.get(first_ip) if first_ip else None
        # Also try exact match for single IPs
        if not matched_details and ip_field in discovered_details:
            matched_details = discovered_details[ip_field]
        if matched_details:
            if not new_row.get("Hostname"):
                new_row["Hostname"] = matched_details["Hostname"]
            if not new_row.get("MAC address"):
                new_row["MAC address"] = matched_details["MAC address"]
            new_row["VLAN ID"] = matched_details["VLAN ID"]
        else:
            # For assets not in active_ot_ips (e.g. range assets), resolve VLAN from first IP
            if first_ip and not new_row.get("VLAN ID"):
                new_row["VLAN ID"] = resolve_vlan_id(first_ip, subnet_to_vlan)
        if "Comment" not in new_row:
            new_row["Comment"] = ""
        if "Quantity" not in new_row:
            new_row["Quantity"] = ""
        if "State" not in new_row:
            new_row["State"] = ""
        final_discovered_assets.append(new_row)

    # Append transitively discovered assets (newly found unique IPs)
    for ip in active_ot_ips:
        if ip not in ot_asset_ips:
            details = discovered_details[ip]
            final_discovered_assets.append({
                "Hostname": details["Hostname"],
                "IP address": ip,
                "MAC address": details["MAC address"],
                "Comment": "Passively Discovered OT-Related Asset",
                "VLAN ID": details["VLAN ID"],
                "Quantity": "",
                "State": ""
            })

    # 5.6. Detect missing VLANs/subnets and append to vlans.csv
    # We want to identify any internal subnet from active_subnets that was not covered by the original VLANs CSV
    loaded_subnets = {str(vn["network"]) for vn in vlan_networks if vn["network"]}
    if os.path.exists(args.vlans):
        try:
            with open(args.vlans, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                headers = [h.strip() for h in (reader.fieldnames or [])]
                cidr_col = "IP Range" if "IP Range" in headers else ("CIDR" if "CIDR" in headers else None)
                if cidr_col:
                    for row in reader:
                        ipr = row.get(cidr_col, "").strip()
                        if ipr:
                            try:
                                loaded_subnets.add(str(ipaddress.ip_network(ipr, strict=False)))
                            except Exception:
                                pass
        except Exception:
            pass

    missing_vlans = {}
    for subnet_str, vid in subnet_to_vlan.items():
        if subnet_str not in loaded_subnets:
            missing_vlans[subnet_str] = vid

    if missing_vlans:
        print(f"netpipeline: Found {len(missing_vlans)} missing VLAN/subnet range(s). Appending to '{args.vlans}'...")
        # Read the existing file headers and rows
        try:
            existing_rows = []
            headers = []
            if os.path.exists(args.vlans):
                with open(args.vlans, mode="r", encoding="utf-8-sig") as f:
                    reader = csv.reader(f)
                    try:
                        headers = next(reader)
                        headers = [h.strip() for h in headers]
                    except StopIteration:
                        pass
                    for row in reader:
                        existing_rows.append(row)
            
            # If empty file or missing headers, default to standard headers
            if not headers:
                headers = ["VLAN ID", "IP Range", "Description", "Zone"]
                
            # Map header fields to columns
            try:
                vlan_id_idx = headers.index("VLAN ID")
            except ValueError:
                headers.append("VLAN ID")
                vlan_id_idx = len(headers) - 1
                
            try:
                ip_range_idx = headers.index("IP Range")
            except ValueError:
                # check CIDR
                if "CIDR" in headers:
                    ip_range_idx = headers.index("CIDR")
                else:
                    headers.append("IP Range")
                    ip_range_idx = len(headers) - 1
                    
            try:
                zone_idx = headers.index("Zone")
            except ValueError:
                headers.append("Zone")
                zone_idx = len(headers) - 1

            # Build set of existing IP Ranges to prevent duplicates
            existing_ranges = set()
            for row in existing_rows:
                if ip_range_idx < len(row):
                    existing_ranges.add(row[ip_range_idx].strip())

            # Append new rows
            new_rows_count = 0
            with open(args.vlans, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                
                # If file was completely empty, write the headers first
                if not existing_rows and os.path.getsize(args.vlans) == 0:
                    writer.writerow(headers)
                    
                for net_str, vid in missing_vlans.items():
                    if net_str not in existing_ranges:
                        # Construct the row matching the headers length
                        row_data = [""] * len(headers)
                        row_data[vlan_id_idx] = vid
                        row_data[ip_range_idx] = net_str
                        row_data[zone_idx] = "" # Leave Zone blank so user can fill it
                        writer.writerow(row_data)
                        new_rows_count += 1
                        
            if new_rows_count > 0:
                print(f"netpipeline: Successfully appended {new_rows_count} new VLAN/subnet row(s) to '{args.vlans}'.")
            else:
                print(f"netpipeline: All discovered subnets are already configured in '{args.vlans}'.")
        except Exception as e:
            print(f"Warning: Failed to append missing VLANs to '{args.vlans}': {e}", file=sys.stderr)

    # 6. Serialization with conflict resolution/append mode
    # A. Output Assets
    final_assets = []
    if args.append and os.path.exists(args.output_assets):
        existing_assets = read_csv_file(args.output_assets)
        existing_dict = {row["IP address"]: row for row in existing_assets if row.get("IP address")}
        
        resolved_ips = {}
        
        for disc_row in final_discovered_assets:
            ip = disc_row.get("IP address", "").strip()
            if not ip:
                final_assets.append(disc_row)
                continue
                
            if ip in existing_dict:
                if ip in resolved_ips:
                    final_assets.append(resolved_ips[ip])
                    continue
                    
                orig_row = existing_dict[ip]
                orig_desc = f"Hostname={orig_row.get('Hostname')}, MAC={orig_row.get('MAC address')}, VLAN={orig_row.get('VLAN ID')}"
                disc_desc = f"Hostname={disc_row.get('Hostname')}, MAC={disc_row.get('MAC address')}, VLAN={disc_row.get('VLAN ID')}"
                
                if (orig_row.get("Hostname") != disc_row.get("Hostname") or 
                    orig_row.get("MAC address") != disc_row.get("MAC address") or 
                    orig_row.get("VLAN ID") != disc_row.get("VLAN ID")):
                    
                    choice = prompt_conflict("Asset", ip, orig_desc, disc_desc)
                    if choice == "1":
                        resolved_row = orig_row
                    elif choice == "2":
                        resolved_row = disc_row
                    else:
                        combined = dict(orig_row)
                        if disc_row.get("Hostname") and disc_row.get("Hostname") != orig_row.get("Hostname"):
                            combined["Hostname"] = f"{orig_row.get('Hostname')}; {disc_row.get('Hostname')}"
                        if disc_row.get("MAC address") and disc_row.get("MAC address") != orig_row.get("MAC address"):
                            combined["MAC address"] = f"{orig_row.get('MAC address')}; {disc_row.get('MAC address')}"
                        resolved_row = combined
                else:
                    resolved_row = orig_row
                    
                resolved_ips[ip] = resolved_row
                final_assets.append(resolved_row)
            else:
                final_assets.append(disc_row)
                
        # Also preserve any existing assets whose IPs are not present in the new set at all
        new_ips = {row.get("IP address", "").strip() for row in final_discovered_assets if row.get("IP address")}
        for ip, orig_row in existing_dict.items():
            if ip not in new_ips:
                final_assets.append(orig_row)
    else:
        final_assets = final_discovered_assets

    # B. Output Flows
    final_flows = []
    flow_keys_seen = set()
    
    if args.append and os.path.exists(args.output_flows):
        existing_flows = read_csv_file(args.output_flows)
        for row in existing_flows:
            src = row.get("IP address source")
            dst = row.get("IP address destination")
            comment = row.get("Comment")
            count = row.get("Count", "1")
            if src and dst and comment:
                final_flows.append({
                    "IP address source": src,
                    "IP address destination": dst,
                    "Comment": comment,
                    "Count": str(count)
                })
                flow_keys_seen.add((src, dst, comment))
                
    for flow in filtered_flows:
        flow_key = (flow["src"], flow["dst"], flow["comment"])
        if flow_key not in flow_keys_seen:
            final_flows.append({
                "IP address source": flow["src"],
                "IP address destination": flow["dst"],
                "Comment": flow["comment"],
                "Count": str(flow["count"])
            })
            flow_keys_seen.add(flow_key)

    # C. Output External Logs
    final_external = []
    ext_keys_seen = set()
    
    if args.append and os.path.exists(args.external):
        existing_ext = read_csv_file(args.external)
        for row in existing_ext:
            ext_ip = row.get("External IP")
            int_ip = row.get("Internal IP")
            proto = row.get("Protocol")
            port = row.get("Port")
            if ext_ip and int_ip and proto and port:
                final_external.append(row)
                ext_keys_seen.add((ext_ip, int_ip, proto, port))
                
    for ext_ip, host, proto, port, int_ip, direction in external_flows_log:
        ext_key = (ext_ip, int_ip, proto, str(port))
        if ext_key not in ext_keys_seen:
            final_external.append({
                "External IP": ext_ip,
                "Hostname": host,
                "Protocol": proto,
                "Port": str(port),
                "Internal IP": int_ip,
                "Direction": direction
            })
            ext_keys_seen.add(ext_key)

    # Save to disk
    # Assets
    try:
        with open(args.output_assets, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["Hostname", "IP address", "MAC address", "Comment", "VLAN ID", "Quantity", "State"], extrasaction="ignore")
            writer.writeheader()
            writer.writerows(final_assets)
        print(f"netpipeline: Successfully saved {len(final_assets)} assets to '{args.output_assets}'.")
    except Exception as e:
        print(f"Error saving assets: {e}", file=sys.stderr)

    # Flows
    try:
        with open(args.output_flows, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["IP address source", "IP address destination", "Comment", "Count"], extrasaction="ignore")
            writer.writeheader()
            writer.writerows(final_flows)
        print(f"netpipeline: Successfully saved {len(final_flows)} flows to '{args.output_flows}'.")
    except Exception as e:
        print(f"Error saving flows: {e}", file=sys.stderr)

    # External WAN logs
    try:
        with open(args.external, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["External IP", "Hostname", "Protocol", "Port", "Internal IP", "Direction"], extrasaction="ignore")
            writer.writeheader()
            writer.writerows(final_external)
        print(f"netpipeline: Successfully saved {len(final_external)} external WAN log entries to '{args.external}'.")
    except Exception as e:
        print(f"Error saving external logs: {e}", file=sys.stderr)

    # Print summary
    print("")
    print("=" * 60)
    print("  netpipeline: Run Summary")
    print("=" * 60)
    print(f"  Packets analysed:       {packet_count}")
    print(f"  Starting assets:        {len(input_assets)}")
    print(f"  Discovered assets:      {len(active_ot_ips) - len(ot_asset_ips)}")
    print(f"  Total assets exported:  {len(final_assets)}")
    print(f"  Flows exported:         {len(final_flows)}")
    print(f"  External WAN entries:   {len(final_external)}")
    if updated_ips_count > 0:
        print(f"  DHCP IP updates:        {updated_ips_count}")
    if missing_vlans:
        print(f"  New VLANs appended:     {len(missing_vlans)}")
    print("=" * 60)

if __name__ == "__main__":
    main()
