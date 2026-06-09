#!/usr/bin/env python3
import os
import sys
import csv
import json
import math
import argparse

# Try loading Pillow for PNG rendering
try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ==============================================================================
# Helper functions for TrueType Fonts (Linux fallback)
# ==============================================================================
def get_pil_font(font_name, size, bold=False):
    if not HAS_PIL:
        return None
    font_paths = []
    if bold:
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ]
    else:
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]
    
    for path in font_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()

# ==============================================================================
# Canvas Architectures
# ==============================================================================
class SVGCanvas:
    def __init__(self, width, height, bg_color):
        self.width = width
        self.height = height
        self.bg_color = bg_color
        self.zones = []
        self.vlans = []
        self.assets = []
        self.flows = []
        
    def draw_rect(self, x, y, w, h, fill_color, border_color, border_width, dashed=False, rx=0, ry=0, layer="assets"):
        dash_attr = ' stroke-dasharray="6,4"' if dashed else ''
        svg_elem = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill_color}" stroke="{border_color}" stroke-width="{border_width}" rx="{rx}" ry="{ry}"{dash_attr} />'
        self.add_to_layer(layer, svg_elem)

    def draw_line(self, x1, y1, x2, y2, color, width, dashed=False, layer="flows"):
        dash_attr = ' stroke-dasharray="6,4"' if dashed else ''
        svg_elem = f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}"{dash_attr} />'
        self.add_to_layer(layer, svg_elem)

    def draw_text(self, x, y, text, font_size, color, align="center", bold=False, italic=False, bg_color=None, layer="assets"):
        weight = ' font-weight="bold"' if bold else ''
        style = ' font-style="italic"' if italic else ''
        anchor = ' text-anchor="middle"' if align == "center" else ' text-anchor="start"' if align == "left" else ' text-anchor="end"'
        escaped_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        dy = ' dy="0.35em"' if align in ("center", "left", "right") else ''
        if bg_color:
            svg_halo = f'<text x="{x}" y="{y}" fill="{bg_color}" stroke="{bg_color}" stroke-width="4" stroke-linejoin="round" font-size="{font_size}"{weight}{style}{anchor}{dy}>{escaped_text}</text>'
            self.add_to_layer(layer, svg_halo)
        svg_elem = f'<text x="{x}" y="{y}" fill="{color}" font-size="{font_size}"{weight}{style}{anchor}{dy}>{escaped_text}</text>'
        self.add_to_layer(layer, svg_elem)

    def draw_path(self, d, color, width, layer="flows"):
        svg_elem = f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" />'
        self.add_to_layer(layer, svg_elem)

    def draw_polygon(self, points, fill_color, border_color, border_width, layer="flows"):
        pts_str = " ".join([f"{x},{y}" for x, y in points])
        svg_elem = f'<polygon points="{pts_str}" fill="{fill_color}" stroke="{border_color}" stroke-width="{border_width}" />'
        self.add_to_layer(layer, svg_elem)

    def add_to_layer(self, layer, elem):
        if layer == "zones":
            self.zones.append(elem)
        elif layer == "vlans":
            self.vlans.append(elem)
        elif layer == "assets":
            self.assets.append(elem)
        else:
            self.flows.append(elem)

    def get_html_wrapper(self):
        svg_content = self.get_svg_string()
        html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>OT Network Diagram</title>
  <style>
    body {{
      margin: 0;
      padding: 0;
      overflow: hidden;
      background-color: #f0f2f5;
      font-family: system-ui, -apple-system, sans-serif;
    }}
    #canvas-container {{
      width: 100vw;
      height: 100vh;
      position: relative;
      cursor: grab;
      overflow: hidden;
    }}
    #canvas-container:active {{
      cursor: grabbing;
    }}
    svg {{
      position: absolute;
      transform-origin: 0 0;
      box-shadow: 0 10px 30px rgba(0,0,0,0.15);
      background: white;
    }}
    #layer-control {{
      position: absolute;
      top: 20px;
      right: 20px;
      background: rgba(255, 255, 255, 0.95);
      padding: 15px;
      border-radius: 8px;
      box-shadow: 0 4px 15px rgba(0,0,0,0.15);
      z-index: 1000;
      border: 1px solid #e2e8f0;
      width: 180px;
    }}
    #layer-control h3 {{
      margin-top: 0;
      margin-bottom: 12px;
      font-size: 14px;
      color: #1e293b;
      font-weight: 600;
      border-bottom: 1px solid #e2e8f0;
      padding-bottom: 6px;
    }}
    .layer-item {{
      display: flex;
      align-items: center;
      margin-bottom: 8px;
      font-size: 12px;
      color: #475569;
      cursor: pointer;
    }}
    .layer-item input {{
      margin-right: 8px;
      cursor: pointer;
    }}
  </style>
</head>
<body>
  <div id="layer-control">
    <h3>Diagram Layers</h3>
    <label class="layer-item">
      <input type="checkbox" id="chk-zones" checked> Zones Background
    </label>
    <label class="layer-item">
      <input type="checkbox" id="chk-vlans" checked> VLAN Boundaries
    </label>
    <label class="layer-item">
      <input type="checkbox" id="chk-assets" checked> Asset Boxes
    </label>
    <label class="layer-item">
      <input type="checkbox" id="chk-flows" checked> Flows & Arrows
    </label>
  </div>

  <div id="canvas-container">
    {svg_content}
  </div>

  <script>
    const svg = document.getElementById('network-svg');
    const container = document.getElementById('canvas-container');
    
    let isPanning = false;
    let startX = 0, startY = 0;
    let translateX = 0, translateY = 0;
    let scale = 1;
    
    function initLayout() {{
      const containerRect = container.getBoundingClientRect();
      const svgWidth = {self.width};
      const svgHeight = {self.height};
      const svgAspect = svgWidth / svgHeight;
      const containerAspect = containerRect.width / containerRect.height;
      
      if (containerAspect > svgAspect) {{
        scale = containerRect.height / svgHeight;
        translateX = (containerRect.width - svgWidth * scale) / 2;
        translateY = 0;
      }} else {{
        scale = containerRect.width / svgWidth;
        translateX = 0;
        translateY = (containerRect.height - svgHeight * scale) / 2;
      }}
      updateTransform();
    }}
    
    function updateTransform() {{
      svg.style.transform = `translate(${{translateX}}px, ${{translateY}}px) scale(${{scale}})`;
    }}
    
    container.addEventListener('mousedown', (e) => {{
      if (e.target === container || svg.contains(e.target)) {{
        isPanning = true;
        startX = e.clientX - translateX;
        startY = e.clientY - translateY;
      }}
    }});
    
    window.addEventListener('mouseup', () => {{
      isPanning = false;
    }});
    
    window.addEventListener('mousemove', (e) => {{
      if (!isPanning) return;
      translateX = e.clientX - startX;
      translateY = e.clientY - startY;
      updateTransform();
    }});
    
    container.addEventListener('wheel', (e) => {{
      e.preventDefault();
      const zoomFactor = 1.1;
      const nextScale = e.deltaY < 0 ? scale * zoomFactor : scale / zoomFactor;
      
      const rect = container.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;
      
      const svgX = (mouseX - translateX) / scale;
      const svgY = (mouseY - translateY) / scale;
      
      scale = Math.min(Math.max(nextScale, 0.05), 15);
      translateX = mouseX - svgX * scale;
      translateY = mouseY - svgY * scale;
      updateTransform();
    }}, {{ passive: false }});
    
    window.addEventListener('resize', initLayout);
    window.addEventListener('load', initLayout);

    // Layer control toggles
    document.getElementById('chk-zones').addEventListener('change', (e) => {{
      document.getElementById('layer-zones').style.opacity = e.target.checked ? '1' : '0.1';
    }});
    document.getElementById('chk-vlans').addEventListener('change', (e) => {{
      document.getElementById('layer-vlans').style.display = e.target.checked ? 'inline' : 'none';
    }});
    document.getElementById('chk-assets').addEventListener('change', (e) => {{
      document.getElementById('layer-assets').style.display = e.target.checked ? 'inline' : 'none';
    }});
    document.getElementById('chk-flows').addEventListener('change', (e) => {{
      document.getElementById('layer-flows').style.display = e.target.checked ? 'inline' : 'none';
    }});
  </script>
</body>
</html>
"""
        return html

    def get_svg_string(self):
        lines = []
        lines.append(f'<svg id="network-svg" width="{self.width}" height="{self.height}" viewBox="0 0 {self.width} {self.height}" xmlns="http://www.w3.org/2000/svg" style="background-color: {self.bg_color}; font-family: system-ui, -apple-system, sans-serif;">')
        
        # Zones Layer
        lines.append('  <g id="layer-zones" style="transition: opacity 0.2s;">')
        for item in self.zones:
            lines.append(f'    {item}')
        lines.append('  </g>')
        
        # VLANs Layer
        lines.append('  <g id="layer-vlans" style="transition: opacity 0.2s;">')
        for item in self.vlans:
            lines.append(f'    {item}')
        lines.append('  </g>')
        
        # Assets Layer
        lines.append('  <g id="layer-assets" style="transition: opacity 0.2s;">')
        for item in self.assets:
            lines.append(f'    {item}')
        lines.append('  </g>')
        
        # Flows Layer
        lines.append('  <g id="layer-flows" style="transition: opacity 0.2s;">')
        for item in self.flows:
            lines.append(f'    {item}')
        lines.append('  </g>')
        
        lines.append('</svg>')
        return "\n".join(lines)


class PILCanvas:
    def __init__(self, width, height, bg_color):
        self.width = int(width)
        self.height = int(height)
        self.bg_color = bg_color
        if not HAS_PIL:
            raise RuntimeError("Pillow is not installed. Run 'pip install Pillow' or use HTML/SVG output.")
        self.image = Image.new("RGB", (self.width, self.height), bg_color)
        self.draw = ImageDraw.Draw(self.image)
        
    def draw_rect(self, x, y, w, h, fill_color, border_color, border_width, dashed=False, rx=0, ry=0, layer="assets"):
        box = [int(x), int(y), int(x + w), int(y + h)]
        fill = None if fill_color == "none" else fill_color
        outline = None if border_color == "none" else border_color
        if hasattr(self.draw, "rounded_rectangle") and (rx > 0 or ry > 0):
            r = max(rx, ry)
            self.draw.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=int(border_width))
        else:
            self.draw.rectangle(box, fill=fill, outline=outline, width=int(border_width))
            
        if dashed:
            self.draw_dashed_line(x, y, x + w, y, border_color, border_width)
            self.draw_dashed_line(x + w, y, x + w, y + h, border_color, border_width)
            self.draw_dashed_line(x + w, y + h, x, y + h, border_color, border_width)
            self.draw_dashed_line(x, y + h, x, y, border_color, border_width)

    def draw_dashed_line(self, x1, y1, x2, y2, color, width=1, dash_length=6, gap_length=4):
        dx = x2 - x1
        dy = y2 - y1
        dist = math.hypot(dx, dy)
        if dist == 0:
            return
        dx /= dist
        dy /= dist
        
        pos = 0
        draw_state = True
        while pos < dist:
            step = dash_length if draw_state else gap_length
            if pos + step > dist:
                step = dist - pos
            next_pos = pos + step
            if draw_state:
                self.draw.line([int(x1 + pos * dx), int(y1 + pos * dy), int(x1 + next_pos * dx), int(y1 + next_pos * dy)], fill=color, width=int(width))
            pos = next_pos
            draw_state = not draw_state

    def draw_line(self, x1, y1, x2, y2, color, width, dashed=False, layer="flows"):
        if dashed:
            self.draw_dashed_line(x1, y1, x2, y2, color, width)
        else:
            self.draw.line([int(x1), int(y1), int(x2), int(y2)], fill=color, width=int(width))

    def draw_text(self, x, y, text, font_size, color, align="center", bold=False, italic=False, bg_color=None, layer="assets"):
        font = get_pil_font(None, font_size, bold)
        try:
            bbox = self.draw.textbbox((0, 0), text, font=font)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
        except AttributeError:
            w, h = self.draw.textsize(text, font=font)
            
        if align == "center":
            tx = x - w/2
            ty = y - h/2
        elif align == "left":
            tx = x
            ty = y - h/2
        else:
            tx = x - w
            ty = y - h/2
            
        if bg_color:
            self.draw.text((int(tx), int(ty)), text, font=font, fill=color, stroke_width=2, stroke_fill=bg_color)
        else:
            self.draw.text((int(tx), int(ty)), text, font=font, fill=color)

    def draw_path(self, d, color, width, layer="flows"):
        parts = d.split()
        last_pt = None
        i = 0
        while i < len(parts):
            cmd = parts[i]
            if cmd == 'M':
                last_pt = (float(parts[i+1]), float(parts[i+2]))
                i += 3
            elif cmd == 'L':
                next_pt = (float(parts[i+1]), float(parts[i+2]))
                if last_pt:
                    self.draw.line([int(last_pt[0]), int(last_pt[1]), int(next_pt[0]), int(next_pt[1])], fill=color, width=int(width))
                last_pt = next_pt
                i += 3
            elif cmd == 'A':
                rx = float(parts[i+1])
                ry = float(parts[i+2])
                x2 = float(parts[i+6])
                y2 = float(parts[i+7])
                x_v = (last_pt[0] + x2) / 2 if last_pt else x2
                y_h = last_pt[1] if last_pt else y2
                box = [int(x_v - rx), int(y_h - ry), int(x_v + rx), int(y_h + ry)]
                self.draw.arc(box, start=180, end=360, fill=color, width=int(width))
                last_pt = (x2, y2)
                i += 8
            else:
                i += 1

    def draw_polygon(self, points, fill_color, border_color, border_width, layer="flows"):
        pts = [(int(x), int(y)) for x, y in points]
        self.draw.polygon(pts, fill=fill_color, outline=border_color, width=int(border_width))

    def save(self, filename):
        self.image.save(filename, "PNG")


# ==============================================================================
# Helper logic to resolve flows
# ==============================================================================
def get_flow_endpoint_vlan(val, ip_to_asset):
    if val == "WAN":
        return "WAN"
    # If the val itself is a VLAN ID in the assets
    if val in [a["VLAN ID"] for a in ip_to_asset.values()]:
        return val
    # If it is a known IP
    if val in ip_to_asset:
        return ip_to_asset[val]["VLAN ID"]
    return None

# ==============================================================================
# Main Generator
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="Create rich Operational Technology (OT) network diagrams from CSVs.")
    parser.add_argument("-a", "--assets", required=True, help="Path to assets CSV file")
    parser.add_argument("-f", "--flows", required=True, help="Path to flows CSV file")
    parser.add_argument("-c", "--config", default="config.json", help="Path to config JSON file")
    parser.add_argument("-o", "--output", help="Path to output diagram file (.html or .png)")
    
    args = parser.parse_args()

    # 1. Load config
    if not os.path.exists(args.config):
        print(f"Error: Config file not found at {args.config}", file=sys.stderr)
        sys.exit(1)
        
    try:
        with open(args.config, "r") as cf:
            config = json.load(cf)
    except Exception as e:
        print(f"Error reading config: {e}", file=sys.stderr)
        sys.exit(1)

    # Validate output format selection
    output_format = config.get("output_format", "png").lower()
    if args.output:
        _, ext = os.path.splitext(args.output.lower())
        if ext == ".html":
            output_format = "html"
        elif ext == ".png":
            output_format = "png"
        else:
            print(f"Warning: Unknown output extension '{ext}', falling back to configured '{output_format}'", file=sys.stderr)

    if output_format == "png" and not HAS_PIL:
        print("Error: Pillow library is required to output PNG format. Please install it with 'pip install Pillow' or change the output format to 'html'.", file=sys.stderr)
        sys.exit(1)

    # 2. Parse Assets CSV
    assets = []
    ip_to_asset = {}
    mac_to_asset = {}
    vlan_to_assets = {}
    vlan_to_zone = {}
    zone_order = config.get("zone_order", ["WAN", "IT", "DMZ", "IACS", "IOT", "Facility"])

    if not os.path.exists(args.assets):
        print(f"Error: Assets CSV file not found at {args.assets}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(args.assets, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            # Normalize headers
            headers = [h.strip() for h in reader.fieldnames]
            reader.fieldnames = headers
            
            # Check mandatory fields
            mandatory = ["Hostname", "IP address", "MAC address", "VLAN ID", "Zone"]
            for field in mandatory:
                if field not in headers:
                    print(f"Error: Assets CSV missing mandatory field '{field}' in headers: {headers}", file=sys.stderr)
                    sys.exit(1)

            for line_no, row in enumerate(reader, start=2):
                row = {k.strip(): v.strip() for k, v in row.items()}
                
                # Check for empty mandatory values
                for field in ["MAC address", "VLAN ID", "Zone"]:
                    if not row.get(field):
                        print(f"Error: Assets CSV (line {line_no}) has empty field '{field}'. Value: {row}", file=sys.stderr)
                        sys.exit(1)

                mac = row["MAC address"]
                vlan_id = row["VLAN ID"]
                zone = row["Zone"]
                
                if zone not in zone_order:
                    print(f"Error: Assets CSV (line {line_no}) has zone '{zone}' which is not defined in config's 'zone_order'.", file=sys.stderr)
                    sys.exit(1)

                # Check VLAN-Zone exclusivity
                if vlan_id in vlan_to_zone and vlan_to_zone[vlan_id] != zone:
                    print(f"Error: VLAN ID '{vlan_id}' belongs to Zone '{vlan_to_zone[vlan_id]}' but is listed in Zone '{zone}' at line {line_no}. A VLAN must only be in one zone.", file=sys.stderr)
                    sys.exit(1)
                
                vlan_to_zone[vlan_id] = zone
                
                # Parse multiple IP addresses (semicolon or comma split)
                ip_field = row["IP address"]
                ips = []
                if ip_field:
                    ips = [ip.strip() for ip in ip_field.replace(",", ";").split(";") if ip.strip()]

                row["IPs"] = ips
                assets.append(row)
                mac_to_asset[mac] = row
                
                for ip in ips:
                    ip_to_asset[ip] = row
                
                if vlan_id not in vlan_to_assets:
                    vlan_to_assets[vlan_id] = []
                vlan_to_assets[vlan_id].append(row)
    except Exception as e:
        print(f"Error parsing assets CSV: {e}", file=sys.stderr)
        sys.exit(1)

    # Ensure WAN zone is present. If it is in zone_order, add the default WAN Gateway asset.
    if "WAN" in zone_order:
        wan_zone_present = "WAN" in vlan_to_zone.values()
        if not wan_zone_present:
            # Inject synthetic WAN asset
            wan_asset = {
                "Hostname": "WAN Gateway",
                "IP address": "WAN",
                "IPs": ["WAN"],
                "MAC address": "WAN",
                "Comment": "External Network / WAN",
                "VLAN ID": "WAN",
                "Zone": "WAN"
            }
            assets.append(wan_asset)
            mac_to_asset["WAN"] = wan_asset
            ip_to_asset["WAN"] = wan_asset
            vlan_to_assets["WAN"] = [wan_asset]
            vlan_to_zone["WAN"] = "WAN"

    # 3. Parse Flows CSV
    flows = []
    if not os.path.exists(args.flows):
        print(f"Error: Flows CSV file not found at {args.flows}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(args.flows, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            headers = [h.strip() for h in reader.fieldnames]
            reader.fieldnames = headers
            
            mandatory = ["IP address source", "IP address destination", "Comment"]
            for field in mandatory:
                if field not in headers:
                    print(f"Error: Flows CSV missing mandatory field '{field}' in headers: {headers}", file=sys.stderr)
                    sys.exit(1)

            for line_no, row in enumerate(reader, start=2):
                row = {k.strip(): v.strip() for k, v in row.items()}
                src = row["IP address source"]
                dst = row["IP address destination"]
                comment = row.get("Comment", "")

                if not src or not dst:
                    print(f"Error: Flows CSV (line {line_no}) has empty source or destination: {row}", file=sys.stderr)
                    sys.exit(1)

                # Validate source reference
                src_vlan = get_flow_endpoint_vlan(src, ip_to_asset)
                if not src_vlan:
                    # check if src matches any VLAN ID directly
                    if src in vlan_to_assets:
                        src_vlan = src
                    else:
                        print(f"Error: Flows CSV (line {line_no}) contains invalid/unknown source '{src}'. It must be an asset IP, a VLAN ID, or 'WAN'.", file=sys.stderr)
                        sys.exit(1)

                # Validate destination reference
                dst_vlan = get_flow_endpoint_vlan(dst, ip_to_asset)
                if not dst_vlan:
                    if dst in vlan_to_assets:
                        dst_vlan = dst
                    else:
                        print(f"Error: Flows CSV (line {line_no}) contains invalid/unknown destination '{dst}'. It must be an asset IP, a VLAN ID, or 'WAN'.", file=sys.stderr)
                        sys.exit(1)

                flows.append({
                    "id": f"flow_{line_no}",
                    "src": src,
                    "dst": dst,
                    "src_vlan": src_vlan,
                    "dst_vlan": dst_vlan,
                    "comment": comment
                })
    except Exception as e:
        print(f"Error parsing flows CSV: {e}", file=sys.stderr)
        sys.exit(1)

    # ==============================================================================
    # Layout Planning
    # ==============================================================================
    # Map out active zones and the VLANs in them
    zone_vlans = {z: [] for z in zone_order}
    for vlan, zone in vlan_to_zone.items():
        if zone in zone_vlans:
            zone_vlans[zone].append(vlan)

    # Barycenter Sweep Sorting: Order VLANs within zones to minimize crossings
    # vlan_pos maps vlan -> horizontal rank [0.0, 1.0]
    vlan_pos = {}
    for zone, vlans in zone_vlans.items():
        # Initialize order
        vlans.sort()
        for i, v in enumerate(vlans):
            vlan_pos[v] = i / max(1, len(vlans) - 1)

    for sweep in range(8):
        for zone in zone_order:
            vlans = zone_vlans[zone]
            if len(vlans) <= 1:
                continue
            
            new_positions = []
            for vlan in vlans:
                connected_positions = []
                for flow in flows:
                    # Only calculate crossings across other zones (inter-zone)
                    if flow["src_vlan"] == vlan and flow["dst_vlan"] != vlan:
                        if flow["dst_vlan"] in vlan_pos:
                            connected_positions.append(vlan_pos[flow["dst_vlan"]])
                    elif flow["dst_vlan"] == vlan and flow["src_vlan"] != vlan:
                        if flow["src_vlan"] in vlan_pos:
                            connected_positions.append(vlan_pos[flow["src_vlan"]])
                
                if connected_positions:
                    avg_pos = sum(connected_positions) / len(connected_positions)
                else:
                    avg_pos = vlan_pos[vlan]
                new_positions.append((vlan, avg_pos))
            
            # Sort VLANs by average gravity position
            new_positions.sort(key=lambda x: x[1])
            zone_vlans[zone] = [item[0] for item in new_positions]
            
            # Update rank
            for i, v in enumerate(zone_vlans[zone]):
                vlan_pos[v] = i / max(1, len(zone_vlans[zone]) - 1)

    # Canvas Dimensions
    dim = config.get("dimensions", {"width": 1200, "height": 1697})
    canvas_w = dim.get("width", 1200)
    canvas_h = dim.get("height", 1697)

    # 4. Proportional Zone Height Sizing
    margin_top = 60
    margin_bottom = 60
    available_h = canvas_h - margin_top - margin_bottom

    # Base weight and proportional content weighting
    # Minimum 120px height per zone, extra height based on assets
    base_zone_h = 120
    allocated_h = {}
    total_assets = max(1, len(assets))
    
    # Calculate assets in each zone
    zone_asset_counts = {z: 0 for z in zone_order}
    for asset in assets:
        z = asset["Zone"]
        if z in zone_asset_counts:
            zone_asset_counts[z] += 1

    remaining_h = available_h - (len(zone_order) * base_zone_h)
    
    # Distribute remaining height
    total_h = 0
    for idx, zone in enumerate(zone_order):
        extra_h = remaining_h * (zone_asset_counts[zone] / total_assets)
        allocated_h[zone] = int(base_zone_h + extra_h)
        total_h += allocated_h[zone]

    # Adjust final zone height to fill available_h exactly
    diff = available_h - total_h
    allocated_h[zone_order[-1]] += diff

    # Set up Y boundaries for each zone
    zone_y_ranges = {}
    curr_y = margin_top
    for zone in zone_order:
        zone_y_ranges[zone] = (curr_y, curr_y + allocated_h[zone])
        curr_y += allocated_h[zone]

    # 5. Geometrical layouts for VLANs and Assets
    vlan_boxes = {}
    asset_boxes = {}
    
    asset_box_w = 142
    asset_box_h = 74
    gap_x = 18
    gap_y = 18
    vlan_pad = 22

    for zone in zone_order:
        y_start, y_end = zone_y_ranges[zone]
        # Top label padding: 35px, Bottom boundary channel: 30px
        usable_y_start = y_start + 35
        usable_y_end = y_end - 30
        zone_h_usable = usable_y_end - usable_y_start

        vlans = zone_vlans[zone]
        if not vlans:
            continue

        # Compute dimensions for each VLAN box
        vlan_widths = {}
        vlan_heights = {}
        vlan_grids = {}

        for vlan in vlans:
            v_assets = vlan_to_assets.get(vlan, [])
            n_assets = len(v_assets)
            
            if n_assets == 0:
                cols, rows = 1, 1
            elif n_assets <= 3:
                cols, rows = n_assets, 1
            elif n_assets == 4:
                cols, rows = 2, 2
            else:
                cols = math.ceil(math.sqrt(n_assets))
                rows = math.ceil(n_assets / cols)

            vlan_grids[vlan] = (cols, rows)
            w_vlan = 2 * vlan_pad + cols * asset_box_w + (cols - 1) * gap_x
            h_vlan = 2 * vlan_pad + rows * asset_box_h + (rows - 1) * gap_y
            vlan_widths[vlan] = w_vlan
            vlan_heights[vlan] = h_vlan

        # Distribute VLANs horizontally in range [100, 1100] (width 1000)
        total_w = sum(vlan_widths.values())
        left_margin = 100
        right_margin = 1100
        net_w = right_margin - left_margin

        if len(vlans) == 1:
            spacing = 0
            start_x = left_margin + (net_w - total_w) / 2
        else:
            spacing = (net_w - total_w) / (len(vlans) + 1)
            # Cap minimum spacing to 20px
            if spacing < 20:
                spacing = 20
            start_x = left_margin + spacing

        for i, vlan in enumerate(vlans):
            w_vlan = vlan_widths[vlan]
            h_vlan = vlan_heights[vlan]
            
            # Center VLAN vertically in the zone's usable Y space
            vlan_y_start = usable_y_start + (zone_h_usable - h_vlan) / 2
            
            x1 = start_x
            y1 = vlan_y_start
            x2 = x1 + w_vlan
            y2 = y1 + h_vlan
            
            vlan_boxes[vlan] = (x1, y1, x2, y2)
            start_x += w_vlan + spacing

            # Layout assets inside this VLAN
            v_assets = vlan_to_assets.get(vlan, [])
            cols, rows = vlan_grids[vlan]
            
            for a_idx, asset in enumerate(v_assets):
                col = a_idx % cols
                row = a_idx // cols
                
                ax1 = x1 + vlan_pad + col * (asset_box_w + gap_x)
                ay1 = y1 + vlan_pad + row * (asset_box_h + gap_y)
                ax2 = ax1 + asset_box_w
                ay2 = ay1 + asset_box_h
                
                asset_boxes[asset["MAC address"]] = (ax1, ay1, ax2, ay2)

    # ==============================================================================
    # Flow Routing
    # ==============================================================================
    # Set up boundary channels & margins lane allocation
    # 5 boundary channels (between consecutive zones)
    boundary_channels = [[] for _ in range(len(zone_order) - 1)]
    intra_zone_channels = [[] for _ in range(len(zone_order))]
    left_margin_flows = []
    right_margin_flows = []

    # Assign flows to channels
    for f in flows:
        sz = vlan_to_zone[f["src_vlan"]]
        dz = vlan_to_zone[f["dst_vlan"]]
        s_idx = zone_order.index(sz)
        d_idx = zone_order.index(dz)

        if s_idx != d_idx:
            # Inter-zone flow
            if abs(s_idx - d_idx) == 1:
                # Adjacent boundary channel
                b_idx = min(s_idx, d_idx)
                boundary_channels[b_idx].append(f["id"])
            else:
                # Bypass flow (uses source and target boundary channels + margin)
                b_src = s_idx if s_idx < d_idx else s_idx - 1
                b_dst = d_idx - 1 if s_idx < d_idx else d_idx
                boundary_channels[b_src].append(f["id"])
                boundary_channels[b_dst].append(f["id"])
                
                # Determine which margin to bypass through
                # Calculate simple horizontal center-of-mass
                cx_s = 600
                if f["src"] in asset_boxes:
                    bx = asset_boxes[f["src"]]
                    cx_s = (bx[0] + bx[2]) / 2
                elif f["src_vlan"] in vlan_boxes:
                    bx = vlan_boxes[f["src_vlan"]]
                    cx_s = (bx[0] + bx[2]) / 2

                cx_d = 600
                if f["dst"] in asset_boxes:
                    bx = asset_boxes[f["dst"]]
                    cx_d = (bx[0] + bx[2]) / 2
                elif f["dst_vlan"] in vlan_boxes:
                    bx = vlan_boxes[f["dst_vlan"]]
                    cx_d = (bx[0] + bx[2]) / 2

                if (cx_s + cx_d) / 2 < canvas_w / 2:
                    left_margin_flows.append(f["id"])
                    f["margin"] = "left"
                else:
                    right_margin_flows.append(f["id"])
                    f["margin"] = "right"
        else:
            # Intra-zone flow
            if f["src_vlan"] != f["dst_vlan"]:
                intra_zone_channels[s_idx].append(f["id"])

    # Allocate lanes
    flow_horizontal_lanes = {}  # (flow_id, channel_idx) -> lane_index
    for c_idx, f_ids in enumerate(boundary_channels):
        for l_idx, f_id in enumerate(f_ids):
            flow_horizontal_lanes[(f_id, c_idx)] = l_idx

    flow_intra_lanes = {}  # flow_id -> lane_index
    for z_idx, f_ids in enumerate(intra_zone_channels):
        for l_idx, f_id in enumerate(f_ids):
            flow_intra_lanes[f_id] = l_idx

    flow_left_margin_lanes = {f_id: idx for idx, f_id in enumerate(left_margin_flows)}
    flow_right_margin_lanes = {f_id: idx for idx, f_id in enumerate(right_margin_flows)}

    # Map VLAN box Y bottom coordinates to place intra-zone channels below them
    zone_max_bottoms = {}
    for zone in zone_order:
        vlans = zone_vlans[zone]
        bottoms = [vlan_boxes[v][3] for v in vlans if v in vlan_boxes]
        zone_max_bottoms[zone] = max(bottoms) if bottoms else zone_y_ranges[zone][0] + 80

    # Build maps of flow connections per box edge to space out endpoints
    top_connections = {}
    bottom_connections = {}
    
    def add_conn(box_key, flow_id, side):
        if side == "top":
            if box_key not in top_connections:
                top_connections[box_key] = []
            top_connections[box_key].append(flow_id)
        else:
            if box_key not in bottom_connections:
                bottom_connections[box_key] = []
            bottom_connections[box_key].append(flow_id)

    for f in flows:
        f_id = f["id"]
        src_val = f["src"]
        dst_val = f["dst"]
        
        src_key = ip_to_asset[src_val]["MAC address"] if src_val in ip_to_asset else ("WAN" if src_val == "WAN" else src_val)
        dst_key = ip_to_asset[dst_val]["MAC address"] if dst_val in ip_to_asset else ("WAN" if dst_val == "WAN" else dst_val)
        
        sz = vlan_to_zone[f["src_vlan"]]
        dz = vlan_to_zone[f["dst_vlan"]]
        s_idx = zone_order.index(sz)
        d_idx = zone_order.index(dz)
        
        if s_idx < d_idx:
            add_conn(src_key, f_id, "bottom")
            add_conn(dst_key, f_id, "top")
        elif s_idx > d_idx:
            add_conn(src_key, f_id, "top")
            add_conn(dst_key, f_id, "bottom")
        else:
            add_conn(src_key, f_id, "bottom")
            add_conn(dst_key, f_id, "bottom")

    def get_conn_x(box_key, bx, side, flow_id):
        conns = top_connections.get(box_key, []) if side == "top" else bottom_connections.get(box_key, [])
        if not conns:
            return (bx[0] + bx[2]) / 2
        conns_sorted = sorted(list(set(conns)))
        if flow_id not in conns_sorted:
            return (bx[0] + bx[2]) / 2
        idx = conns_sorted.index(flow_id)
        spacing = (bx[2] - bx[0]) / (len(conns_sorted) + 1)
        return bx[0] + spacing * (idx + 1)

    # Draw coordinate paths for each flow
    flow_paths = {}
    for f in flows:
        f_id = f["id"]
        src_val = f["src"]
        dst_val = f["dst"]
        
        # Get Source box
        if src_val in ip_to_asset:
            bx_s = asset_boxes[ip_to_asset[src_val]["MAC address"]]
        elif src_val == "WAN":
            bx_s = asset_boxes["WAN"]
        else:
            bx_s = vlan_boxes[src_val]

        # Get Destination box
        if dst_val in ip_to_asset:
            bx_d = asset_boxes[ip_to_asset[dst_val]["MAC address"]]
        elif dst_val == "WAN":
            bx_d = asset_boxes["WAN"]
        else:
            bx_d = vlan_boxes[dst_val]

        src_key = ip_to_asset[src_val]["MAC address"] if src_val in ip_to_asset else ("WAN" if src_val == "WAN" else src_val)
        dst_key = ip_to_asset[dst_val]["MAC address"] if dst_val in ip_to_asset else ("WAN" if dst_val == "WAN" else dst_val)

        sz = vlan_to_zone[f["src_vlan"]]
        dz = vlan_to_zone[f["dst_vlan"]]
        s_idx = zone_order.index(sz)
        d_idx = zone_order.index(dz)

        # Get shifted horizontal connection coordinates to prevent overlaps
        if s_idx < d_idx:
            cx_s = get_conn_x(src_key, bx_s, "bottom", f_id)
            cx_d = get_conn_x(dst_key, bx_d, "top", f_id)
        elif s_idx > d_idx:
            cx_s = get_conn_x(src_key, bx_s, "top", f_id)
            cx_d = get_conn_x(dst_key, bx_d, "bottom", f_id)
        else:
            cx_s = get_conn_x(src_key, bx_s, "bottom", f_id)
            cx_d = get_conn_x(dst_key, bx_d, "bottom", f_id)

        path_pts = []

        if s_idx < d_idx:
            # Source above destination (downwards flow)
            start_pt = (cx_s, bx_s[3])
            end_pt = (cx_d, bx_d[1])
            
            if d_idx == s_idx + 1:
                # Adjacent
                j = flow_horizontal_lanes[(f_id, s_idx)]
                y_lane = zone_y_ranges[sz][1] - 15 + j * 10
                path_pts = [start_pt, (cx_s, y_lane), (cx_d, y_lane), end_pt]
            else:
                # Bypass margin routing
                j1 = flow_horizontal_lanes[(f_id, s_idx)]
                j2 = flow_horizontal_lanes[(f_id, d_idx - 1)]
                y_start = zone_y_ranges[sz][1] - 15 + j1 * 10
                y_end = zone_y_ranges[dz][0] - 15 + j2 * 10
                
                if f["margin"] == "left":
                    m_lane = flow_left_margin_lanes[f_id]
                    x_bypass = 70 - m_lane * 10
                else:
                    m_lane = flow_right_margin_lanes[f_id]
                    x_bypass = 1130 + m_lane * 10
                    
                path_pts = [
                    start_pt,
                    (cx_s, y_start),
                    (x_bypass, y_start),
                    (x_bypass, y_end),
                    (cx_d, y_end),
                    end_pt
                ]
        elif s_idx > d_idx:
            # Source below destination (upwards flow)
            start_pt = (cx_s, bx_s[1])
            end_pt = (cx_d, bx_d[3])
            
            if s_idx == d_idx + 1:
                # Adjacent
                j = flow_horizontal_lanes[(f_id, d_idx)]
                y_lane = zone_y_ranges[dz][1] - 15 + j * 10
                path_pts = [start_pt, (cx_s, y_lane), (cx_d, y_lane), end_pt]
            else:
                # Bypass margin routing
                j1 = flow_horizontal_lanes[(f_id, s_idx - 1)]
                j2 = flow_horizontal_lanes[(f_id, d_idx)]
                y_start = zone_y_ranges[sz][0] - 15 + j1 * 10
                y_end = zone_y_ranges[dz][1] - 15 + j2 * 10
                
                if f["margin"] == "left":
                    m_lane = flow_left_margin_lanes[f_id]
                    x_bypass = 70 - m_lane * 10
                else:
                    m_lane = flow_right_margin_lanes[f_id]
                    x_bypass = 1130 + m_lane * 10
                    
                path_pts = [
                    start_pt,
                    (cx_s, y_start),
                    (x_bypass, y_start),
                    (x_bypass, y_end),
                    (cx_d, y_end),
                    end_pt
                ]
        else:
            # Same zone flow
            if f["src_vlan"] != f["dst_vlan"]:
                # Different VLANs, route below the VLAN boxes inside the zone
                start_pt = (cx_s, bx_s[3])
                end_pt = (cx_d, bx_d[3])
                j = flow_intra_lanes[f_id]
                y_lane = zone_max_bottoms[sz] + 15 + j * 10
                path_pts = [start_pt, (cx_s, y_lane), (cx_d, y_lane), end_pt]
            else:
                # Same VLAN flow (simple internal routing loop)
                start_pt = (cx_s, bx_s[3])
                end_pt = (cx_d, bx_d[3])
                y_lane = bx_s[3] + 12
                path_pts = [start_pt, (cx_s, y_lane), (cx_d, y_lane), end_pt]

        flow_paths[f_id] = path_pts

    # Detect segment intersections for bridge humps
    horizontal_segments = []
    vertical_segments = []
    
    for f_id, path in flow_paths.items():
        for i in range(len(path) - 1):
            p1 = path[i]
            p2 = path[i+1]
            if p1[1] == p2[1]:  # Horizontal
                horizontal_segments.append({
                    "flow_id": f_id,
                    "y": p1[1],
                    "x1": min(p1[0], p2[0]),
                    "x2": max(p1[0], p2[0])
                })
            elif p1[0] == p2[0]:  # Vertical
                vertical_segments.append({
                    "flow_id": f_id,
                    "x": p1[0],
                    "y1": min(p1[1], p2[1]),
                    "y2": max(p1[1], p2[1])
                })

    # Record crossing intersections on horizontal segments
    crossings = {}  # flow_id -> list of X coordinates
    for h in horizontal_segments:
        h_flow = h["flow_id"]
        y = h["y"]
        x1 = h["x1"]
        x2 = h["x2"]
        
        for v in vertical_segments:
            v_flow = v["flow_id"]
            if h_flow == v_flow:
                continue
            x = v["x"]
            y1 = v["y1"]
            y2 = v["y2"]
            
            # Intersection check
            if (x1 < x < x2) and (y1 < y < y2):
                if h_flow not in crossings:
                    crossings[h_flow] = []
                crossings[h_flow].append(x)

    # ==============================================================================
    # Rendering Stage
    # ==============================================================================
    # Select canvas backend
    bg_theme = config.get("theme", "light")
    bg_color = "#ffffff" if bg_theme == "light" else "#121212"
    
    if output_format == "html":
        canvas = SVGCanvas(canvas_w, canvas_h, bg_color)
    else:
        canvas = PILCanvas(canvas_w, canvas_h, bg_color)

    # 1. Render Zones
    zones_styles = config.get("zones", {})
    for zone in zone_order:
        y_start, y_end = zone_y_ranges[zone]
        style = zones_styles.get(zone, {
            "fill": "#fafafa",
            "stroke": "#d1d5db",
            "text_color": "#1f2937",
            "label": zone
        })
        
        # Bordering zone boxes spanning [50, 1150]
        canvas.draw_rect(
            50, y_start, canvas_w - 100, y_end - y_start,
            fill_color=style["fill"],
            border_color=style["stroke"],
            border_width=1,
            rx=0, ry=0,
            layer="zones"
        )
        
        # Zone header label
        canvas.draw_text(
            60, y_start + 18,
            text=style.get("label", zone),
            font_size=13,
            color=style["text_color"],
            align="left",
            bold=True,
            layer="zones"
        )

    # 2. Render VLAN Boundaries
    vlan_style = config.get("styles", {}).get("vlan_border", {
        "stroke": "#3b82f6",
        "width": 2,
        "dasharray": "6,4"
    })
    
    for vlan, box in vlan_boxes.items():
        x1, y1, x2, y2 = box
        canvas.draw_rect(
            x1, y1, x2 - x1, y2 - y1,
            fill_color="none",
            border_color=vlan_style["stroke"],
            border_width=vlan_style["width"],
            dashed=True,
            rx=6, ry=6,
            layer="vlans"
        )
        
        # VLAN label text
        canvas.draw_text(
            x1 + 10, y1 + 10,
            text=f"VLAN {vlan}" if vlan != "WAN" else "WAN Interface",
            font_size=10,
            color=vlan_style["stroke"],
            align="left",
            bold=True,
            layer="vlans"
        )

    # 3. Render Asset Boxes
    asset_style = config.get("styles", {}).get("asset", {
        "fill": "#ffffff",
        "stroke": "#94a3b8",
        "text_color": "#0f172a",
        "ip_color": "#1d4ed8",
        "mac_color": "#64748b"
    })

    for asset in assets:
        mac = asset["MAC address"]
        if mac not in asset_boxes:
            continue
            
        x1, y1, x2, y2 = asset_boxes[mac]
        
        # Draw box container
        canvas.draw_rect(
            x1, y1, x2 - x1, y2 - y1,
            fill_color=asset_style["fill"],
            border_color=asset_style["stroke"],
            border_width=1.5,
            rx=4, ry=4,
            layer="assets"
        )
        
        # Write asset info
        # 1. Hostname (bold)
        canvas.draw_text(
            (x1 + x2)/2, y1 + 14,
            text=asset["Hostname"],
            font_size=10,
            color=asset_style["text_color"],
            align="center",
            bold=True,
            layer="assets"
        )
        
        # 2. IP Address (emphasized)
        ips_str = ", ".join(asset["IPs"])
        canvas.draw_text(
            (x1 + x2)/2, y1 + 28,
            text=ips_str,
            font_size=9,
            color=asset_style["ip_color"],
            align="center",
            bold=True,
            layer="assets"
        )
        
        # 3. MAC address (smaller/subtle)
        canvas.draw_text(
            (x1 + x2)/2, y1 + 42,
            text=asset["MAC address"],
            font_size=8,
            color=asset_style["mac_color"],
            align="center",
            layer="assets"
        )
        
        # 4. Comment (small italic)
        comment = asset.get("Comment", "")
        # Truncate comment if too long
        if len(comment) > 22:
            comment = comment[:20] + ".."
            
        if comment:
            canvas.draw_text(
                (x1 + x2)/2, y1 + 56,
                text=comment,
                font_size=7.5,
                color=asset_style["mac_color"],
                align="center",
                italic=True,
                layer="assets"
            )

    # 4. Render Flow Lines with Bridge Humps and Arrowheads
    flow_style = config.get("styles", {}).get("flow", {
        "stroke": "#d84315",
        "width": 1.5,
        "arrow_size": 6
    })
    
    for f in flows:
        f_id = f["id"]
        path = flow_paths[f_id]
        f_crossings = crossings.get(f_id, [])
        
        # Draw path
        d_parts = [f"M {path[0][0]} {path[0][1]}"]
        R = 5  # Hump radius
        
        for i in range(len(path) - 1):
            p1 = path[i]
            p2 = path[i+1]
            
            if p1[1] == p2[1]:  # Horizontal segment
                y = p1[1]
                x1 = p1[0]
                x2 = p2[0]
                
                # Check intersections on this segment
                seg_cross = [cx for cx in f_crossings if min(x1, x2) < cx < max(x1, x2)]
                
                if seg_cross:
                    if x1 < x2:
                        seg_cross.sort()
                        sign = 1
                    else:
                        seg_cross.sort(reverse=True)
                        sign = -1
                        
                    for cx in seg_cross:
                        # Draw line up to the hump
                        d_parts.append(f"L {cx - R * sign} {y}")
                        # Draw hump (sweep is 0 for L-to-R, 1 for R-to-L)
                        sweep = 0 if sign == 1 else 1
                        d_parts.append(f"A {R} {R} 0 0 {sweep} {cx + R * sign} {y}")
                    d_parts.append(f"L {x2} {y}")
                else:
                    d_parts.append(f"L {x2} {y}")
            else:  # Vertical segment
                d_parts.append(f"L {p2[0]} {p2[1]}")
                
        d_path = " ".join(d_parts)
        canvas.draw_path(d_path, color=flow_style["stroke"], width=flow_style["width"], layer="flows")
        
        # Arrowhead at the end
        end_pt = path[-1]
        prev_pt = path[-2]
        arrow_size = flow_style.get("arrow_size", 6)
        
        dx = end_pt[0] - prev_pt[0]
        dy = end_pt[1] - prev_pt[1]
        
        if dy > 0:  # Enters top (pointing down)
            arrow_pts = [
                (end_pt[0] - arrow_size/2, end_pt[1] - arrow_size),
                (end_pt[0], end_pt[1]),
                (end_pt[0] + arrow_size/2, end_pt[1] - arrow_size)
            ]
        elif dy < 0:  # Enters bottom (pointing up)
            arrow_pts = [
                (end_pt[0] - arrow_size/2, end_pt[1] + arrow_size),
                (end_pt[0], end_pt[1]),
                (end_pt[0] + arrow_size/2, end_pt[1] + arrow_size)
            ]
        elif dx > 0:  # Enters left (pointing right)
            arrow_pts = [
                (end_pt[0] - arrow_size, end_pt[1] - arrow_size/2),
                (end_pt[0], end_pt[1]),
                (end_pt[0] - arrow_size, end_pt[1] + arrow_size/2)
            ]
        else:  # Enters right (pointing left)
            arrow_pts = [
                (end_pt[0] + arrow_size, end_pt[1] - arrow_size/2),
                (end_pt[0], end_pt[1]),
                (end_pt[0] + arrow_size, end_pt[1] + arrow_size/2)
            ]
            
        canvas.draw_polygon(arrow_pts, flow_style["stroke"], flow_style["stroke"], 1, layer="flows")

        # Centered flow comments
        # Find the longest horizontal segment in the path to draw text neatly
        longest_h = None
        max_h_len = -1
        for i in range(len(path) - 1):
            p1 = path[i]
            p2 = path[i+1]
            if p1[1] == p2[1]:
                length = abs(p2[0] - p1[0])
                if length > max_h_len:
                    max_h_len = length
                    longest_h = (p1, p2)
                    
        comment_text = f.get("comment", "")
        if comment_text:
            label_bg = bg_color
            if longest_h:
                p1, p2 = longest_h
                x_mid = (p1[0] + p2[0]) / 2
                y = p1[1]
                for z, (y_start, y_end) in zone_y_ranges.items():
                    if y_start <= y <= y_end:
                        label_bg = config.get("zones", {}).get(z, {}).get("fill", bg_color)
                        if label_bg == "none":
                            label_bg = bg_color
                        break
                canvas.draw_text(
                    x_mid, y,
                    text=comment_text,
                    font_size=8,
                    color=flow_style["stroke"],
                    align="center",
                    italic=True,
                    bg_color=label_bg,
                    layer="flows"
                )
            else:
                mid = len(path) // 2
                p = path[mid]
                y = p[1]
                for z, (y_start, y_end) in zone_y_ranges.items():
                    if y_start <= y <= y_end:
                        label_bg = config.get("zones", {}).get(z, {}).get("fill", bg_color)
                        if label_bg == "none":
                            label_bg = bg_color
                        break
                canvas.draw_text(
                    p[0] + 6, p[1],
                    text=comment_text,
                    font_size=8,
                    color=flow_style["stroke"],
                    align="left",
                    italic=True,
                    bg_color=label_bg,
                    layer="flows"
                )

    # 6. Save output
    default_out = "map.html" if output_format == "html" else "map.png"
    out_filename = args.output if args.output else default_out
    
    if output_format == "html":
        # Render HTML wrapper
        html_content = canvas.get_html_wrapper()
        try:
            with open(out_filename, "w", encoding="utf-8") as out_f:
                out_f.write(html_content)
            print(f"Successfully generated interactive HTML network diagram: {out_filename}")
        except Exception as e:
            print(f"Error saving HTML diagram: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # Render PNG image
        try:
            canvas.save(out_filename)
            print(f"Successfully generated high-resolution PNG network diagram: {out_filename}")
        except Exception as e:
            print(f"Error saving PNG diagram: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
