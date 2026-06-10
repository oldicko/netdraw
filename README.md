# OT Network Diagram Generator (`netdraw`)

A Python utility that generates rich, circuit-diagram-style network drawings for Operational Technology (OT) environments. By parsing simple asset and flow CSV files, it produces high-resolution static PNGs or interactive, self-contained HTML/SVG diagrams with zoom, pan, and layer control capabilities.

![Example Network Diagram](map.png)

---

## Features

- **Standard OT Security Zones**: Stacks zones vertically (e.g., WAN, IT, DMZ, IACS, IoT, Facility) according to the Purdue Model. The canvas dynamically calculates and scales to the minimum required grid of portrait A4 pages (supporting both multi-column and multi-row page grids) based on asset density.
- **Horizontal VLAN Sorting**: Auto-arranges VLANs side-by-side using a barycenter sweep heuristic to minimize flow line crossovers.
- **Asset Grid Layout**: Places devices inside their dashed VLAN borders in a clean 2D grid. Packs up to 5 assets horizontally per row to minimize vertical height. Supports multi-homed devices with multiple IP addresses.
- **Orthogonal Flow Routing**: Routes connections vertically and horizontally through dedicated channels between zones and VLANs. Automatically selects the shortest route, prioritizing direct vertical channels when clear of other assets, or utilizing page-join bypass corridors (with print-margin protection) and dynamic local margin bypasses.
- **Lane Allocation**: Spreads parallel horizontal flows apart inside channels to prevent lines from overlapping.
- **Bridge Humps**: Automatically detects line crossings and renders semicircular arch "humps" at intersection points (supported in both SVG/HTML and PNG outputs).
- **Margin & Corridor Bypass**: Routes long-distance flows through vertical side corridors, local right-margin bypasses, or central corridors between page fold boundaries to keep the main diagram clean.
- **Interactive HTML Canvas**: Embeds mouse-drag panning, mouse-wheel zooming, and check-boxes to toggle diagram layers (Zones, VLANs, Assets, Flows) fully offline.
- **Custom Styling**: Shading, borders, typography, line weights, and colors are fully configurable through a JSON file.

---


## Installation

`netdraw` requires **Python 3.x** and uses standard library modules.

### Requirements

To generate static **PNG** files, install the Pillow library:

```bash
pip install Pillow
```

*Note: Generating **HTML** diagrams has zero external dependencies.*

---

## Offline Security & Privacy

This utility is designed with **zero network dependency** and **strict privacy** in mind. It is safe for use on air-gapped systems or secure environments:

- **100% Local Execution**: The script operates entirely on your local machine. It does not initiate any internet connections or transmit data to external services.
- **Zero External CDNs/APIs**: The generated interactive HTML file is completely self-contained. It embeds standard SVG elements and standard system font styling. It uses plain, vanilla JavaScript with no remote libraries, tracker scripts, or external dependencies (like Google Fonts or external CDNs).
- **Inspectable Source Code**: Written in plain Python using standard library modules (plus the optional local `Pillow` library). You can easily review the code to verify that no networking or sockets are used.

---

## Usage

Run `netdraw.py` from the command line by passing paths to your assets CSV, flows CSV, and config JSON.

### Generate Interactive HTML (with Pan-Zoom & Layers)

```bash
./netdraw.py -a sample_assets.csv -f sample_flows.csv -c config.json -o map.html
```

### Generate High-Resolution PNG

```bash
./netdraw.py -a sample_assets.csv -f sample_flows.csv -c config.json -o map.png
```

### CLI Arguments

- `-a`, `--assets`: Path to the assets CSV file (Required).
- `-f`, `--flows`: Path to the flows CSV file (Required).
- `-c`, `--config`: Path to the configuration JSON file (Default: `config.json`).
- `-o`, `--output`: Path to the output file (Generates `.html` or `.png` based on the file extension).

---

## CSV File Formats

### 1. Assets CSV File (`assets.csv`)

Defines all network nodes, their IPs, MAC addresses, VLANs, and security zones.

| Header | Description | Example |
| :--- | :--- | :--- |
| **Hostname** | Name of the asset | `PLC-1` |
| **IP address** | IP address(es) (Semicolon `;` separated for multi-homed hosts) | `192.168.1.10;192.168.1.11` |
| **MAC address** | MAC Address of the asset (Acts as the unique primary key) | `00:50:56:a1:b2:c3` |
| **Comment** | Optional description or device details | `Siemens S7-1500 PLC` |
| **VLAN ID** | The VLAN number or ID containing the asset | `200` |
| **Zone** | Security Zone matching config's `zone_order` | `IACS` |

#### Sample Assets File:
```csv
Hostname,IP address,MAC address,Comment,VLAN ID,Zone
IT-Server-1,10.10.1.10,00:11:22:33:44:55,Primary Domain Controller,10,IT
DMZ-Web,172.16.1.10,00:aa:bb:cc:dd:01,External Web Server,100,DMZ
PLC-1,192.168.1.10,00:50:56:a1:b2:c3,Siemens S7,200,IACS
```

### 2. Flows CSV File (`flows.csv`)

Defines data flows and connections. Endpoint values can be specific **IP addresses** (for asset-to-asset flows) or **VLAN IDs** (for VLAN-to-VLAN or asset-to-VLAN boundary flows).

| Header | Description | Example |
| :--- | :--- | :--- |
| **IP address source** | Flow source endpoint (Asset IP, VLAN ID, or `WAN`) | `192.168.1.10` or `200` or `WAN` |
| **IP address destination** | Flow destination endpoint (Asset IP, VLAN ID, or `WAN`) | `10.10.1.10` or `100` or `WAN` |
| **Comment** | Optional label shown centered on the flow line | `Modbus TCP` |

#### Sample Flows File:
```csv
IP address source,IP address destination,Comment
10.10.1.50,10.10.1.10,AD Authentication
192.168.1.50,192.168.1.10,Modbus TCP
210,200,SCADA Polling
WAN,172.16.1.20,HTTPS Web access
```

---

## Configuration (`config.json`)

Configure layout dimensions, default output type, zone order, colors, and line widths.

```json
{
  "theme": "light",
  "output_format": "png",
  "dimensions": {
    "width": 1200,
    "height": 1697
  },
  "zone_order": ["WAN", "IT", "DMZ", "IACS", "IOT", "Facility"],
  "zones": {
    "WAN": {
      "fill": "#eceff1",
      "stroke": "#b0bec5",
      "text_color": "#37474f",
      "label": "Wide Area Network (WAN)"
    },
    "IACS": {
      "fill": "#e8eaf6",
      "stroke": "#9fa8da",
      "text_color": "#1a237e",
      "label": "Industrial Control Systems (Zone 3)"
    }
  },
  "styles": {
    "font_family": "system-ui, -apple-system, sans-serif",
    "vlan_border": {
      "stroke": "#42a5f5",
      "width": 2,
      "dasharray": "6,4"
    },
    "asset": {
      "fill": "#ffffff",
      "stroke": "#90caf9",
      "text_color": "#1565c0",
      "ip_color": "#0d47a1",
      "mac_color": "#546e7a"
    },
    "flow": {
      "stroke": "#1976d2",
      "width": 1.5,
      "arrow_size": 6
    }
  }
}
```

---

## Validation & Errors

The generator performs strict verification to prevent broken maps:
- **Zone Exclusivity**: If a VLAN ID is declared in multiple zones across assets, `netdraw` will abort and print the conflicting lines.
- **Reference Integrity**: If a flow endpoint references an IP or VLAN not declared in the assets CSV (and not equal to the special `WAN` keyword), a verbose error is generated pinpointing the row and missing value.
- **Mandatory Fields**: Aborts with detailed messages if headers or essential values are missing.
