#!/usr/bin/env python3
# ==============================================================================
# OT Network Diagram Generator (netdraw)
#
# SECURITY & PRIVACY NOTICE:
# This script processes sensitive network configuration (assets, VLANs, flows)
# and is designed to run 100% OFFLINE.
# - No outbound or inbound network connections are made.
# - No telemetry, tracker, or external requests are initiated.
# - Generated HTML/SVG outputs are completely self-contained (no remote CDNs/APIs).
# ==============================================================================
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
        self.labels = []
        self.lens_col_name = "State"
        self.states = {}
        self.lens_enabled_by_default = False
        
    def draw_rect(self, x, y, w, h, fill_color, border_color, border_width, dashed=False, rx=0, ry=0, layer="assets", state_attrs=None):
        dash_attr = ' stroke-dasharray="6,4"' if dashed else ''
        state_str = ""
        class_str = ""
        if state_attrs:
            class_str = ' class="lens-target"'
            state_str = " " + " ".join([f'{k}="{v}"' for k, v in state_attrs.items()])
        svg_elem = f'<rect{class_str} x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill_color}" stroke="{border_color}" stroke-width="{border_width}" rx="{rx}" ry="{ry}"{dash_attr}{state_str} />'
        self.add_to_layer(layer, svg_elem)

    def draw_line(self, x1, y1, x2, y2, color, width, dashed=False, layer="flows"):
        dash_attr = ' stroke-dasharray="6,4"' if dashed else ''
        svg_elem = f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}"{dash_attr} />'
        self.add_to_layer(layer, svg_elem)

    def draw_text(self, x, y, text, font_size, color, align="center", bold=False, italic=False, bg_color=None, bg_style="rect", layer="assets", state_attrs=None):
        weight = ' font-weight="bold"' if bold else ''
        style = ' font-style="italic"' if italic else ''
        anchor = ' text-anchor="middle"' if align == "center" else ' text-anchor="start"' if align == "left" else ' text-anchor="end"'
        escaped_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        dy = ' dy="0.35em"' if align in ("center", "left", "right") else ''
        
        state_str = ""
        class_str = ""
        if state_attrs:
            class_str = ' class="lens-target"'
            state_str = " " + " ".join([f'{k}="{v}"' for k, v in state_attrs.items()])
            
        if bg_color:
            if bg_style == "halo":
                svg_halo = f'<text x="{x}" y="{y}" fill="{bg_color}" stroke="{bg_color}" stroke-width="4" stroke-linejoin="round" font-size="{font_size}"{weight}{style}{anchor}{dy}>{escaped_text}</text>'
                self.add_to_layer(layer, svg_halo)
            else:
                char_width = 0.58 if bold else 0.52
                text_w = len(text) * font_size * char_width
                text_h = font_size * 1.25
                pad_x = 4
                pad_y = 2
                w = text_w + 2 * pad_x
                h = text_h + 2 * pad_y
                
                if align == "center":
                    rx = x - w / 2
                elif align == "left":
                    rx = x - pad_x
                else:
                    rx = x - w + pad_x
                ry = y - h / 2
                
                svg_bg = f'<rect x="{rx}" y="{ry}" width="{w}" height="{h}" fill="{bg_color}" stroke="none" />'
                self.add_to_layer(layer, svg_bg)
            
        svg_elem = f'<text{class_str} x="{x}" y="{y}" fill="{color}" font-size="{font_size}"{weight}{style}{anchor}{dy}{state_str}>{escaped_text}</text>'
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
        elif layer == "labels":
            self.labels.append(elem)
        else:
            self.flows.append(elem)

    def get_html_wrapper(self):
        svg_content = self.get_svg_string()
        lens_checked_attr = 'checked' if self.lens_enabled_by_default else ''
        
        # Build Legend items dynamically
        legend_items = []
        for state_name, style in self.states.items():
            fill = style.get("fill", "#fff")
            stroke = style.get("stroke", "#000")
            legend_items.append(f"""
      <div style="display: flex; align-items: center; margin-bottom: 4px; font-size: 11px; color: #475569;">
        <span style="display: inline-block; width: 12px; height: 12px; background-color: {fill}; border: 1.5px solid {stroke}; margin-right: 6px; border-radius: 2px;"></span>
        <span>{state_name}</span>
      </div>
            """)
        legend_html = "\n".join(legend_items)
        
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
    .lens-target {{
      transition: fill 0.2s, stroke 0.2s, opacity 0.2s;
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
    
    <label class="layer-item" style="border-top: 1px solid #e2e8f0; margin-top: 12px; padding-top: 12px; font-weight: bold;">
      <input type="checkbox" id="chk-lens" {lens_checked_attr}> {self.lens_col_name} Lens
    </label>
    
    <div id="lens-legend" style="margin-top: 10px; display: {'block' if self.lens_enabled_by_default else 'none'}; padding-left: 4px;">
      <h4 style="margin: 0 0 6px 0; font-size: 11px; color: #475569; font-weight: 600;">{self.lens_col_name} Legend</h4>
      {legend_html}
    </div>
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

    // Layer control toggles
    document.getElementById('chk-zones').addEventListener('change', (e) => {{
      document.getElementById('layer-zones').style.opacity = e.target.checked ? '1' : '0.1';
      document.getElementById('layer-labels').style.opacity = e.target.checked ? '1' : '0.1';
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

    // Lens toggling logic
    const chkLens = document.getElementById('chk-lens');
    const legend = document.getElementById('lens-legend');
    
    function applyLens() {{
      const active = chkLens.checked;
      legend.style.display = active ? 'block' : 'none';
      
      const targets = document.querySelectorAll('.lens-target');
      targets.forEach(el => {{
        const state = el.getAttribute('data-state');
        if (active) {{
          if (state) {{
            // Apply state colors
            if (el.tagName === 'rect') {{
              const stateFill = el.getAttribute('data-state-fill');
              const stateStroke = el.getAttribute('data-state-stroke');
              if (stateFill) el.setAttribute('fill', stateFill);
              if (stateStroke) el.setAttribute('stroke', stateStroke);
            }} else if (el.tagName === 'text') {{
              const stateColor = el.getAttribute('data-state-color');
              if (stateColor) el.setAttribute('fill', stateColor);
            }}
            el.style.opacity = '1';
          }} else {{
            // Dim elements without a state
            el.style.opacity = '0.3';
          }}
        }} else {{
          // Revert to defaults
          if (el.tagName === 'rect') {{
            const defaultFill = el.getAttribute('data-default-fill');
            const defaultStroke = el.getAttribute('data-default-stroke');
            if (defaultFill) el.setAttribute('fill', defaultFill);
            if (defaultStroke) el.setAttribute('stroke', defaultStroke);
          }} else if (el.tagName === 'text') {{
            const defaultColor = el.getAttribute('data-default-color');
            if (defaultColor) el.setAttribute('fill', defaultColor);
          }}
          el.style.opacity = '1';
        }}
      }});
    }}
    
    chkLens.addEventListener('change', applyLens);
    
    window.addEventListener('DOMContentLoaded', () => {{
      initLayout();
      applyLens();
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
        
        # Labels Layer (rendered on top of flows, transitions with zones)
        lines.append('  <g id="layer-labels" style="transition: opacity 0.2s;">')
        for item in self.labels:
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
        
    def draw_rect(self, x, y, w, h, fill_color, border_color, border_width, dashed=False, rx=0, ry=0, layer="assets", state_attrs=None):
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

    def draw_text(self, x, y, text, font_size, color, align="center", bold=False, italic=False, bg_color=None, bg_style="rect", layer="assets", state_attrs=None):
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
            if bg_style == "halo":
                self.draw.text((int(tx), int(ty)), text, font=font, fill=color, stroke_width=2, stroke_fill=bg_color)
            else:
                pad_x = 4
                pad_y = 2
                rx1 = tx - pad_x
                ry1 = ty - pad_y
                rx2 = tx + w + pad_x
                ry2 = ty + h + pad_y
                self.draw.rectangle([int(rx1), int(ry1), int(rx2), int(ry2)], fill=bg_color)
            
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
# Helper logic to resolve flows and match IP ranges
# ==============================================================================
def ip_to_int(ip):
    try:
        parts = [int(p) for p in ip.strip().split('.')]
        if len(parts) == 4 and all(0 <= p <= 255 for p in parts):
            return (parts[0] << 24) + (parts[1] << 16) + (parts[2] << 8) + parts[3]
    except Exception:
        pass
    return None

def ip_in_cidr(ip, cidr):
    ip_val = ip_to_int(ip)
    if ip_val is None:
        return False
    try:
        net_part, mask_part = cidr.split('/')
        net_val = ip_to_int(net_part)
        mask_len = int(mask_part)
        if net_val is not None and 0 <= mask_len <= 32:
            if mask_len == 0:
                return True
            mask = (0xFFFFFFFF << (32 - mask_len)) & 0xFFFFFFFF
            return (ip_val & mask) == (net_val & mask)
    except Exception:
        pass
    return False

def ip_in_hyphen_range(ip, hyphen_range):
    ip_val = ip_to_int(ip)
    if ip_val is None:
        return False
    try:
        start_str, end_str = hyphen_range.split('-')
        start_str = start_str.strip()
        end_str = end_str.strip()
        
        start_val = ip_to_int(start_str)
        if start_val is None:
            return False
            
        if '.' not in end_str:
            start_parts = start_str.split('.')
            end_parts = end_str.split('.')
            num_end_parts = len(end_parts)
            merged_parts = start_parts[:-num_end_parts] + end_parts
            end_str = '.'.join(merged_parts)
            
        end_val = ip_to_int(end_str)
        if end_val is None:
            return False
            
        return start_val <= ip_val <= end_val
    except Exception:
        pass
    return False

def match_ip_to_asset(val, ip_to_asset, assets):
    # 1. Direct match in ip_to_asset
    if val in ip_to_asset:
        return ip_to_asset[val]
        
    # 2. Check CIDR or range matches
    for asset in assets:
        for ip_field in asset.get("IPs", []):
            if '/' in ip_field:
                if ip_in_cidr(val, ip_field):
                    return asset
            elif '-' in ip_field:
                if ip_in_hyphen_range(val, ip_field):
                    return asset
    return None

def get_flow_endpoint_vlan(val, ip_to_asset, assets):
    if val == "WAN":
        return "WAN"
    # If the val itself is a VLAN ID in the assets
    if val in [a["VLAN ID"] for a in assets]:
        return val
    # If it is a matched IP
    matched = match_ip_to_asset(val, ip_to_asset, assets)
    if matched:
        return matched["VLAN ID"]
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
    parser.add_argument("-v", "--vlans", help="Path to VLANs CSV file (optional)")
    parser.add_argument("--lens", action="store_true", help="Enable generic state coloring lens by default / for static PNG")
    
    args = parser.parse_args()

    print("netdraw: Running in offline mode (all data processed locally, zero network connections).")

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

    states_config = config.get("states", {})
    lens_col_name = config.get("lens_column", "State")

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

    # 1.5 Parse VLANs CSV if provided
    vlan_to_ip_range = {}
    if args.vlans:
        if not os.path.exists(args.vlans):
            print(f"Error: VLANs CSV file not found at {args.vlans}", file=sys.stderr)
            sys.exit(1)
        try:
            with open(args.vlans, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                headers = [h.strip() for h in reader.fieldnames]
                reader.fieldnames = headers
                cidr_col = "IP Range" if "IP Range" in headers else ("CIDR" if "CIDR" in headers else None)
                if "VLAN ID" not in headers or not cidr_col:
                    print(f"Error: VLANs CSV must contain 'VLAN ID' and 'IP Range' or 'CIDR' in headers: {headers}", file=sys.stderr)
                    sys.exit(1)
                for row in reader:
                    row = {k.strip(): v.strip() for k, v in row.items()}
                    vid = row["VLAN ID"]
                    ipr = row[cidr_col]
                    if vid and ipr:
                        vlan_to_ip_range[vid] = ipr
        except Exception as e:
            print(f"Error parsing VLANs CSV: {e}", file=sys.stderr)
            sys.exit(1)

    # 2. Parse Assets CSV
    assets = []
    ip_to_asset = {}
    mac_to_asset = {}
    vlan_to_assets = {}
    vlan_to_zone = {}
    zone_order = config.get("zone_order", ["WAN", "IT", "DMZ", "IACS", "IOT", "Facility"])
    lens_col_name = config.get("lens_column", "State")

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
            mandatory = ["Hostname", "IP address", "VLAN ID", "Zone"]
            for field in mandatory:
                if field not in headers:
                    print(f"Error: Assets CSV missing mandatory field '{field}' in headers: {headers}", file=sys.stderr)
                    sys.exit(1)

            for line_no, row in enumerate(reader, start=2):
                row = {k.strip(): v.strip() for k, v in row.items()}
                
                hostname = row.get("Hostname", "")
                mac = row.get("MAC address", "").strip()
                vlan_id = row.get("VLAN ID", "").strip()
                zone = row.get("Zone", "").strip()
                qty = row.get("Quantity", "").strip()
                state = row.get(lens_col_name, "").strip()
                
                # Check for empty mandatory values (MAC address is optional if Quantity is set)
                if not mac and not qty:
                    print(f"Error: Assets CSV (line {line_no}) has empty MAC address and no Quantity is set. Value: {row}", file=sys.stderr)
                    sys.exit(1)
                
                if not vlan_id or not zone:
                    print(f"Error: Assets CSV (line {line_no}) has empty VLAN ID or Zone. Value: {row}", file=sys.stderr)
                    sys.exit(1)

                if not mac:
                    # Generate a unique synthetic MAC address
                    safe_hostname = hostname.replace(" ", "_")
                    mac = f"AUTO_MAC_{line_no}_{safe_hostname}"
                    row["MAC address"] = mac
                
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
                row["Quantity"] = qty
                row["State"] = state
                assets.append(row)
                mac_to_asset[mac] = row
                
                for ip in ips:
                    ip_to_asset[ip] = row
                
                if vlan_id not in vlan_to_assets:
                    vlan_to_assets[vlan_id] = []
                vlan_to_assets[vlan_id].append(row)

                # Validation warning if IP does not belong to VLAN range
                if vlan_id in vlan_to_ip_range:
                    vlan_cidr = vlan_to_ip_range[vlan_id]
                    for ip in ips:
                        if '/' not in ip and '-' not in ip and ip != "WAN" and ip_to_int(ip) is not None:
                            if not ip_in_cidr(ip, vlan_cidr):
                                print(f"Warning: Asset '{hostname}' (line {line_no}) has IP '{ip}' which does not belong to VLAN {vlan_id} range '{vlan_cidr}'", file=sys.stderr)
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
                "Zone": "WAN",
                "Quantity": "",
                "State": ""
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
                src_vlan = get_flow_endpoint_vlan(src, ip_to_asset, assets)
                if not src_vlan:
                    # check if src matches any VLAN ID directly
                    if src in vlan_to_assets:
                        src_vlan = src
                    else:
                        print(f"Error: Flows CSV (line {line_no}) contains invalid/unknown source '{src}'. It must be an asset IP, a VLAN ID, or 'WAN'.", file=sys.stderr)
                        sys.exit(1)

                # Validate destination reference
                dst_vlan = get_flow_endpoint_vlan(dst, ip_to_asset, assets)
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

    # 4. Geometrical layouts for VLANs and Assets: Grid Structure Determination
    vlan_grids = {}
    vlan_widths = {}
    vlan_heights = {}
    vlan_pad_x = {}
    vlan_pad_y = {}
    asset_grid_pos = {}
    asset_grid_pos["WAN"] = (0, 1)

    asset_box_w = 142
    asset_box_h = 74
    gap_x = 18
    gap_y = 18
    
    # Pre-populate same-VLAN flows to determine dynamic padding
    vlan_same_flows = {}
    for f in flows:
        f_id = f["id"]
        if f["src_vlan"] == f["dst_vlan"]:
            vlan = f["src_vlan"]
            if vlan not in vlan_same_flows:
                vlan_same_flows[vlan] = []
            vlan_same_flows[vlan].append(f_id)

    for zone in zone_order:
        vlans = zone_vlans[zone]
        for vlan in vlans:
            v_assets = vlan_to_assets.get(vlan, [])
            n_assets = len(v_assets)
            
            if n_assets == 0:
                cols, rows = 1, 1
            elif n_assets <= 5:
                cols, rows = n_assets, 1
            else:
                cols = math.ceil(math.sqrt(n_assets))
                rows = math.ceil(n_assets / cols)

            vlan_grids[vlan] = (cols, rows)
            # Calculate dynamic vertical padding based on same-VLAN flows
            v_flows = vlan_same_flows.get(vlan, [])
            if v_flows:
                # We need enough padding to clear all same-VLAN lanes
                # Each lane takes 8px, plus base offset of 10px, plus 15px clearance
                n_lanes = len(v_flows)
                vlan_pad_y_val = max(22, 10 + (n_lanes - 1) * 8 + 15)
            else:
                vlan_pad_y_val = 22
                
            # If any asset in row 0 has a quantity (meaning it is a stacked box),
            # increase vlan_pad_y_val by 8px to keep clearance.
            has_stacked_on_row_0 = any(
                (a_idx // cols == 0) and asset.get("Quantity")
                for a_idx, asset in enumerate(v_assets)
            )
            if has_stacked_on_row_0:
                vlan_pad_y_val += 8
                
            vlan_pad_x_val = 22
            vlan_pad_x[vlan] = vlan_pad_x_val
            vlan_pad_y[vlan] = vlan_pad_y_val
            
            w_vlan = 2 * vlan_pad_x_val + cols * asset_box_w + (cols - 1) * gap_x
            
            # Dynamically expand w_vlan if the VLAN label is wider than the grid
            label_text = f"VLAN {vlan} - {vlan_to_ip_range[vlan]}" if vlan in vlan_to_ip_range else f"VLAN {vlan}"
            estimated_label_w = len(label_text) * 8.0 + 40
            if w_vlan < estimated_label_w:
                w_vlan = estimated_label_w
                
            h_vlan = 2 * vlan_pad_y_val + rows * asset_box_h + (rows - 1) * gap_y
            vlan_widths[vlan] = w_vlan
            vlan_heights[vlan] = h_vlan

            for a_idx, asset in enumerate(v_assets):
                row = a_idx // cols
                asset_grid_pos[asset["MAC address"]] = (row, rows)

    # 5. Geometrical layouts: X Coordinates Calculation & Multi-Page A4 Canvas Width
    num_pages_x = 1
    page_w = 1200
    canvas_h = 1697  # Initialized; will be dynamically expanded to multiple of page_h later
    vlan_boxes = {}
    asset_boxes = {}

    while True:
        canvas_w = num_pages_x * page_w
        x_margins = [page_w * k for k in range(1, num_pages_x)]
        right_margin = canvas_w - 100
        fits = True
        
        vlan_boxes = {}
        asset_boxes = {}
        
        for zone in zone_order:
            vlans = zone_vlans[zone]
            if not vlans:
                continue
                
            spacing = 40
            start_x = 100 + spacing
            
            for vlan in vlans:
                w_vlan = vlan_widths[vlan]
                x1 = start_x
                x2 = x1 + w_vlan
                
                # Check and push repeatedly to avoid all X margins (page joins)
                overlapping = True
                while overlapping:
                    overlapping = False
                    for x_m in x_margins:
                        if not (x2 <= x_m - 45 or x1 >= x_m + 45):
                            x1 = x_m + 45
                            x2 = x1 + w_vlan
                            overlapping = True
                            break
                            
                if x2 > right_margin:
                    fits = False
                    break
                    
                vlan_boxes[vlan] = [x1, 0, x2, 0]
                start_x = x2 + spacing
                
                v_assets = vlan_to_assets.get(vlan, [])
                cols, rows = vlan_grids[vlan]
                for a_idx, asset in enumerate(v_assets):
                    col = a_idx % cols
                    row = a_idx // cols
                    ax1 = x1 + vlan_pad_x[vlan] + col * (asset_box_w + gap_x)
                    ax2 = ax1 + asset_box_w
                    asset_boxes[asset["MAC address"]] = [ax1, 0, ax2, 0]
                    
            if not fits:
                break
                
        if fits:
            break
        else:
            num_pages_x += 1

    # Position the WAN asset box, centering it but avoiding X margins
    if "WAN" in asset_boxes:
        x_c = canvas_w / 2
        for x_m in x_margins:
            if abs(x_c - x_m) < 100:
                x_c = x_m - 100
                break
        asset_boxes["WAN"] = [x_c - 71, 0, x_c + 71, 0]

    # Calculate rightmost content boundary
    max_content_x = 0
    if vlan_boxes:
        max_content_x = max(max_content_x, max(bx[2] for bx in vlan_boxes.values()))
    if asset_boxes:
        max_content_x = max(max_content_x, max(bx[2] for bx in asset_boxes.values()))

    # 6. Flow Routing Assignment (pre-calculate channels and lanes)
    boundary_channels = [[] for _ in range(len(zone_order) - 1)]
    left_margin_flows = []
    right_margin_flows = []

    def get_preferred_side(box_key, target_zone_idx, current_zone_idx):
        if box_key in asset_grid_pos:
            row, rows = asset_grid_pos[box_key]
            if rows > 1:
                if row < rows / 2:
                    return "top"
                else:
                    return "bottom"
        if current_zone_idx < target_zone_idx:
            return "bottom"
        else:
            return "top"

    def get_flow_sides(f):
        src_val = f["src"]
        dst_val = f["dst"]
        matched_src = match_ip_to_asset(src_val, ip_to_asset, assets)
        matched_dst = match_ip_to_asset(dst_val, ip_to_asset, assets)
        src_key = matched_src["MAC address"] if matched_src else ("WAN" if src_val == "WAN" else src_val)
        dst_key = matched_dst["MAC address"] if matched_dst else ("WAN" if dst_val == "WAN" else dst_val)
        
        sz = vlan_to_zone[f["src_vlan"]]
        dz = vlan_to_zone[f["dst_vlan"]]
        s_idx = zone_order.index(sz)
        d_idx = zone_order.index(dz)
        
        if f["src_vlan"] == f["dst_vlan"]:
            r_s, _ = asset_grid_pos.get(src_key, (0, 1))
            r_d, _ = asset_grid_pos.get(dst_key, (0, 1))
            if r_s == r_d:
                side = "top" if r_s == 0 else "bottom"
                return side, side
            else:
                if r_s > r_d:
                    return "top", "bottom"
                else:
                    return "bottom", "top"
        else:
            src_side = get_preferred_side(src_key, d_idx, s_idx)
            dst_side = get_preferred_side(dst_key, s_idx, d_idx)
            return src_side, dst_side

    # Assign flows to channels
    for f in flows:
        f_id = f["id"]
        src_val = f["src"]
        dst_val = f["dst"]
        
        matched_src = match_ip_to_asset(src_val, ip_to_asset, assets)
        matched_dst = match_ip_to_asset(dst_val, ip_to_asset, assets)
        bx_s = asset_boxes.get(matched_src["MAC address"] if matched_src else src_val)
        bx_d = asset_boxes.get(matched_dst["MAC address"] if matched_dst else dst_val)
        
        # fallback if not found
        if not bx_s:
            bx_s = vlan_boxes.get(src_val, [0, 0, 0, 0])
        if not bx_d:
            bx_d = vlan_boxes.get(dst_val, [0, 0, 0, 0])
            
        sz = vlan_to_zone[f["src_vlan"]]
        dz = vlan_to_zone[f["dst_vlan"]]
        s_idx = zone_order.index(sz)
        d_idx = zone_order.index(dz)
        
        if f["src_vlan"] == f["dst_vlan"]:
            pass
        else:
            src_side, dst_side = get_flow_sides(f)
            c_src = max(0, s_idx - 1) if src_side == "top" else min(len(zone_order) - 2, s_idx)
            c_dst = max(0, d_idx - 1) if dst_side == "top" else min(len(zone_order) - 2, d_idx)
            
            if c_src == c_dst:
                boundary_channels[c_src].append(f_id)
            else:
                boundary_channels[c_src].append(f_id)
                boundary_channels[c_dst].append(f_id)
                
                cx_s = (bx_s[0] + bx_s[2]) / 2
                cx_d = (bx_d[0] + bx_d[2]) / 2
                
                p_src = int(cx_s // 1200)
                p_dst = int(cx_d // 1200)
                
                if p_src != p_dst:
                    left_margin_flows.append(f_id)
                    f["margin"] = "left"
                elif p_src == 1:
                    right_margin_flows.append(f_id)
                    f["margin"] = "right"
                else:
                    avg_x = (cx_s + cx_d) / 2
                    if avg_x < 600:
                        left_margin_flows.append(f_id)
                        f["margin"] = "left"
                    else:
                        right_margin_flows.append(f_id)
                        f["margin"] = "right"

    # Pre-populate same VLAN flow lanes
    flow_same_vlan_lanes = {}
    for vlan, f_ids in vlan_same_flows.items():
        for l_idx, f_id in enumerate(f_ids):
            flow_same_vlan_lanes[f_id] = l_idx

    # 7. Compute Dynamic Zone Heights and Clearances
    margin_top = 45
    margin_bottom = 45
    
    zone_clearances = {}
    zone_min_heights = {}
    
    for idx, zone in enumerate(zone_order):
        vlans = zone_vlans[zone]
        if not vlans:
            zone_min_heights[zone] = 120
            zone_clearances[zone] = (25, 20)
            continue
            
        N_chan_above = len(boundary_channels[idx - 1]) if idx > 0 else 0
        N_chan_below = len(boundary_channels[idx]) if idx < len(zone_order) - 1 else 0
        
        N_same_top_max = 0
        N_same_bottom_max = 0
        
        for vlan in vlans:
            v_flows = vlan_same_flows.get(vlan, [])
            l_top_max = -1
            l_bottom_max = -1
            for f_id in v_flows:
                # Find the flow object
                f_obj = next(fl for fl in flows if fl["id"] == f_id)
                s_side, d_side = get_flow_sides(f_obj)
                l_idx = flow_same_vlan_lanes[f_id]
                if s_side == "top" and d_side == "top":
                    l_top_max = max(l_top_max, l_idx)
                elif s_side == "bottom" and d_side == "bottom":
                    l_bottom_max = max(l_bottom_max, l_idx)
            
            N_same_top_max = max(N_same_top_max, l_top_max + 1)
            N_same_bottom_max = max(N_same_bottom_max, l_bottom_max + 1)
            
        # Clearances calculations to guarantee no overlaps:
        pad_top = 20 + N_chan_above * 8 + N_same_top_max * 8
        pad_bottom = 16 + N_chan_below * 8 + N_same_bottom_max * 8
        
        zone_clearances[zone] = (pad_top, pad_bottom)
        
        # Max VLAN height in this zone
        max_vlan_h = max(vlan_heights[v] for v in vlans)
        zone_min_heights[zone] = pad_top + max_vlan_h + pad_bottom

    # Determine canvas_h dynamically as a multiple of 1697 (page_h)
    num_pages_y = 1
    page_h = 1697
    
    while True:
        canvas_h = num_pages_y * page_h
        y_margins = [page_h * k for k in range(1, num_pages_y)]
        
        zone_y_ranges = {}
        allocated_h = {}
        curr_y = margin_top
        fits = True
        
        for idx, zone in enumerate(zone_order):
            vlans = zone_vlans[zone]
            pad_top, pad_bottom = zone_clearances[zone]
            max_vlan_h = max(vlan_heights[v] for v in vlans) if vlans else 0
            
            # If the current Y coordinate (top boundary of the zone) overlaps any Y margin, push it past
            for y_m in y_margins:
                if abs(curr_y - y_m) < 45:
                    curr_y = y_m + 45
            
            # Check if the VLAN box range [curr_y + pad_top, curr_y + pad_top + max_vlan_h] overlaps any Y margin
            for y_m in y_margins:
                vlan_start = curr_y + pad_top
                vlan_end = vlan_start + max_vlan_h
                if not (vlan_end <= y_m - 45 or vlan_start >= y_m + 45):
                    # Overlaps! Push the entire zone to start after the margin
                    curr_y = y_m + 45
                    break
            
            # In our margin-avoidance layout, each zone is allocated its minimum required height
            h_zone = zone_min_heights[zone]
            
            zone_y_ranges[zone] = (curr_y, curr_y + h_zone)
            allocated_h[zone] = h_zone
            curr_y += h_zone
            
        if curr_y > canvas_h - margin_bottom:
            fits = False
            
        if fits:
            break
        else:
            num_pages_y += 1

    # 8. Geometrical layouts: Assign Y Coordinates
    for zone in zone_order:
        y_start, y_end = zone_y_ranges[zone]
        vlans = zone_vlans[zone]
        if not vlans:
            continue
            
        pad_top, pad_bottom = zone_clearances[zone]
        max_vlan_h = max(vlan_heights[v] for v in vlans)
        
        # Distribute the zone's extra height (over its minimum required height) evenly
        zone_extra = allocated_h[zone] - zone_min_heights[zone]
        
        for vlan in vlans:
            h_vlan = vlan_heights[vlan]
            # Center the VLAN box within the zone's vertical height, adjusted for clearances
            y1 = y_start + pad_top + zone_extra / 2 + (max_vlan_h - h_vlan) / 2
            y2 = y1 + h_vlan
            
            vlan_boxes[vlan][1] = y1
            vlan_boxes[vlan][3] = y2
            
            v_assets = vlan_to_assets.get(vlan, [])
            cols, rows = vlan_grids[vlan]
            
            for a_idx, asset in enumerate(v_assets):
                row = a_idx // cols
                ay1 = y1 + vlan_pad_y[vlan] + row * (asset_box_h + gap_y)
                ay2 = ay1 + asset_box_h
                
                mac = asset["MAC address"]
                asset_boxes[mac][1] = ay1
                asset_boxes[mac][3] = ay2

    # WAN box Y coordinates (WAN Gateway is in WAN zone)
    if "WAN" in asset_boxes:
        y_start, y_end = zone_y_ranges["WAN"]
        # Center it in WAN zone
        asset_boxes["WAN"][1] = y_start + (allocated_h["WAN"] - asset_box_h) / 2
        asset_boxes["WAN"][3] = asset_boxes["WAN"][1] + asset_box_h
        
        # Center the WAN VLAN boundary box around the WAN Gateway asset box
        if "WAN" in vlan_boxes:
            ax1, ay1, ax2, ay2 = asset_boxes["WAN"]
            pad_x = vlan_pad_x.get("WAN", 22)
            pad_y = vlan_pad_y.get("WAN", 22)
            vlan_boxes["WAN"] = [ax1 - pad_x, ay1 - pad_y, ax2 + pad_x, ay2 + pad_y]

    # Allocate lanes
    flow_horizontal_lanes = {}  # (flow_id, channel_idx) -> lane_index
    for c_idx, f_ids in enumerate(boundary_channels):
        for l_idx, f_id in enumerate(f_ids):
            flow_horizontal_lanes[(f_id, c_idx)] = l_idx

    flow_left_margin_lanes = {f_id: idx for idx, f_id in enumerate(left_margin_flows)}
    flow_right_margin_lanes = {f_id: idx for idx, f_id in enumerate(right_margin_flows)}

    # Build connection port maps for spacing
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
        
        matched_src = match_ip_to_asset(src_val, ip_to_asset, assets)
        matched_dst = match_ip_to_asset(dst_val, ip_to_asset, assets)
        src_key = matched_src["MAC address"] if matched_src else ("WAN" if src_val == "WAN" else src_val)
        dst_key = matched_dst["MAC address"] if matched_dst else ("WAN" if dst_val == "WAN" else dst_val)
        
        src_side, dst_side = get_flow_sides(f)
        add_conn(src_key, f_id, src_side)
        add_conn(dst_key, f_id, dst_side)

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
        matched_src = match_ip_to_asset(src_val, ip_to_asset, assets)
        if matched_src:
            bx_s = asset_boxes[matched_src["MAC address"]]
        elif src_val == "WAN":
            bx_s = asset_boxes["WAN"]
        else:
            bx_s = vlan_boxes[src_val]

        # Get Destination box
        matched_dst = match_ip_to_asset(dst_val, ip_to_asset, assets)
        if matched_dst:
            bx_d = asset_boxes[matched_dst["MAC address"]]
        elif dst_val == "WAN":
            bx_d = asset_boxes["WAN"]
        else:
            bx_d = vlan_boxes[dst_val]

        src_key = matched_src["MAC address"] if matched_src else ("WAN" if src_val == "WAN" else src_val)
        dst_key = matched_dst["MAC address"] if matched_dst else ("WAN" if dst_val == "WAN" else dst_val)

        sz = vlan_to_zone[f["src_vlan"]]
        dz = vlan_to_zone[f["dst_vlan"]]
        s_idx = zone_order.index(sz)
        d_idx = zone_order.index(dz)

        src_side, dst_side = get_flow_sides(f)
        cx_s = get_conn_x(src_key, bx_s, src_side, f_id)
        cx_d = get_conn_x(dst_key, bx_d, dst_side, f_id)

        path_pts = []

        if f["src_vlan"] == f["dst_vlan"]:
            # Same VLAN flow: route internally using row-based gap/offset
            r_s, rows_s = asset_grid_pos.get(src_key, (0, 1))
            r_d, rows_d = asset_grid_pos.get(dst_key, (0, 1))
            l_idx = flow_same_vlan_lanes.get(f_id, 0)
            
            if r_s == r_d:
                if r_s == 0:
                    start_pt = (cx_s, bx_s[1])
                    end_pt = (cx_d, bx_d[1])
                    y_lane = bx_s[1] - 10 - l_idx * 8
                else:
                    start_pt = (cx_s, bx_s[3])
                    end_pt = (cx_d, bx_d[3])
                    y_lane = bx_s[3] + 10 + l_idx * 8
                path_pts = [start_pt, (cx_s, y_lane), (cx_d, y_lane), end_pt]
            else:
                if r_s > r_d:
                    start_pt = (cx_s, bx_s[1])
                    end_pt = (cx_d, bx_d[3])
                    y_lane = (bx_s[1] + bx_d[3]) / 2 + (l_idx - 1) * 8
                else:
                    start_pt = (cx_s, bx_s[3])
                    end_pt = (cx_d, bx_d[1])
                    y_lane = (bx_s[3] + bx_d[1]) / 2 + (l_idx - 1) * 8
                path_pts = [start_pt, (cx_s, y_lane), (cx_d, y_lane), end_pt]
        else:
            # Inter-VLAN flow: route via boundary channels and margins
            c_src = max(0, s_idx - 1) if src_side == "top" else min(len(zone_order) - 2, s_idx)
            c_dst = max(0, d_idx - 1) if dst_side == "top" else min(len(zone_order) - 2, d_idx)
            
            start_pt = (cx_s, bx_s[1] if src_side == "top" else bx_s[3])
            end_pt = (cx_d, bx_d[1] if dst_side == "top" else bx_d[3])
            
            if c_src == c_dst:
                j = flow_horizontal_lanes[(f_id, c_src)]
                N = len(boundary_channels[c_src])
                y_boundary = zone_y_ranges[zone_order[c_src]][1]
                if N <= 1:
                    y_lane = y_boundary - 12
                else:
                    half = N // 2
                    if j < half:
                        y_lane = y_boundary - 12 - (half - 1 - j) * 8
                    else:
                        y_lane = y_boundary + 12 + (j - half) * 8
                path_pts = [start_pt, (cx_s, y_lane), (cx_d, y_lane), end_pt]
            else:
                j1 = flow_horizontal_lanes[(f_id, c_src)]
                j2 = flow_horizontal_lanes[(f_id, c_dst)]
                N_src = len(boundary_channels[c_src])
                N_dst = len(boundary_channels[c_dst])
                
                y_bound_src = zone_y_ranges[zone_order[c_src]][1]
                if N_src <= 1:
                    y_start = y_bound_src - 12
                else:
                    half_src = N_src // 2
                    if j1 < half_src:
                        y_start = y_bound_src - 12 - (half_src - 1 - j1) * 8
                    else:
                        y_start = y_bound_src + 12 + (j1 - half_src) * 8
                        
                y_bound_dst = zone_y_ranges[zone_order[c_dst]][1]
                if N_dst <= 1:
                    y_end = y_bound_dst - 12
                else:
                    half_dst = N_dst // 2
                    if j2 < half_dst:
                        y_end = y_bound_dst - 12 - (half_dst - 1 - j2) * 8
                    else:
                        y_end = y_bound_dst + 12 + (j2 - half_dst) * 8
                
                p_src = int(cx_s // 1200)
                p_dst = int(cx_d // 1200)
                
                if p_src != p_dst:
                    x_m = 1200 * max(p_src, p_dst)
                    m_lane = flow_left_margin_lanes.get(f_id, flow_right_margin_lanes.get(f_id, 0))
                    x_bypass = x_m - 65 - m_lane * 10
                else:
                    p = p_src
                    z_start = min(s_idx, d_idx)
                    z_end = max(s_idx, d_idx)
                    
                    def is_col_free(x):
                        for z in range(z_start + 1, z_end):
                            zone = zone_order[z]
                            for asset_mac, box in asset_boxes.items():
                                if asset_mac == "WAN":
                                    continue
                                if asset_mac in mac_to_asset:
                                    asset_zone = mac_to_asset[asset_mac]["Zone"]
                                    if asset_zone == zone:
                                        if box[0] - 15 <= x <= box[2] + 15:
                                            return False
                        return True
                    
                    if is_col_free(cx_d):
                        x_bypass = cx_d
                    elif is_col_free(cx_s):
                        x_bypass = cx_s
                    else:
                        if f["margin"] == "left":
                            m_lane = flow_left_margin_lanes[f_id]
                            if p == 0:
                                x_bypass = 35 - m_lane * 10
                            else:
                                x_m = 1200 * p
                                x_bypass = x_m - 65 - m_lane * 10
                        else:
                            m_lane = flow_right_margin_lanes[f_id]
                            if p == num_pages_x - 1:
                                # Calculate local optimal x_bypass to avoid colliding with VLAN boxes
                                x_bypass = max(cx_s, cx_d) + 30 + m_lane * 10
                                while True:
                                    overlap_found = False
                                    for z in range(z_start, z_end + 1):
                                        zone = zone_order[z]
                                        for v in zone_vlans[zone]:
                                            if v in vlan_boxes:
                                                x1_v, _, x2_v, _ = vlan_boxes[v]
                                                if int(x2_v // 1200) == p:
                                                    if x1_v - 25 <= x_bypass <= x2_v + 25:
                                                        x_bypass = x2_v + 30 + m_lane * 10
                                                        overlap_found = True
                                                        break
                                        if overlap_found:
                                            break
                                    if not overlap_found:
                                        break
                                
                                # Avoid page joins (x_margins)
                                for x_m in x_margins:
                                    if abs(x_bypass - x_m) < 45:
                                        x_bypass = x_m + 45
                            else:
                                x_m = 1200 * (p + 1)
                                x_bypass = x_m - 65 - m_lane * 10
                    
                path_pts = [
                    start_pt,
                    (cx_s, y_start),
                    (x_bypass, y_start),
                    (x_bypass, y_end),
                    (cx_d, y_end),
                    end_pt
                ]
                
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
                    "seg_idx": i,
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

    # Record crossing intersections on specific horizontal segments
    crossings = {}  # (flow_id, seg_idx) -> list of X coordinates
    for h in horizontal_segments:
        h_flow = h["flow_id"]
        seg_idx = h["seg_idx"]
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
                h_path = flow_paths[h_flow]
                v_path = flow_paths[v_flow]
                
                dist_h_start = math.hypot(x - h_path[0][0], y - h_path[0][1])
                dist_h_end = math.hypot(x - h_path[-1][0], y - h_path[-1][1])
                dist_v_start = math.hypot(x - v_path[0][0], y - v_path[0][1])
                dist_v_end = math.hypot(x - v_path[-1][0], y - v_path[-1][1])
                
                if min(dist_h_start, dist_h_end, dist_v_start, dist_v_end) < 25:
                    continue
                    
                key = (h_flow, seg_idx)
                if key not in crossings:
                    crossings[key] = []
                if x not in crossings[key]:
                    crossings[key].append(x)

    # ==============================================================================
    # Rendering Stage
    # ==============================================================================
    # Select canvas backend
    bg_theme = config.get("theme", "light")
    bg_color = "#ffffff" if bg_theme == "light" else "#121212"
    
    if output_format == "html":
        canvas = SVGCanvas(canvas_w, canvas_h, bg_color)
        canvas.states = states_config
        canvas.lens_col_name = lens_col_name
        canvas.lens_enabled_by_default = args.lens
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
        
        # (Zone label rendering moved to the end of main to ensure it draws on top of flows for masking)
        pass

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
        if vlan == "WAN":
            vlan_label = "WAN Interface"
        else:
            if vlan in vlan_to_ip_range:
                vlan_label = f"VLAN {vlan} - {vlan_to_ip_range[vlan]}"
            else:
                vlan_label = f"VLAN {vlan}"
                
        canvas.draw_text(
            x1 + 10, y1 + 10,
            text=vlan_label,
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

    static_lens_active = (output_format == "png" and args.lens)

    for asset in assets:
        mac = asset["MAC address"]
        if mac not in asset_boxes:
            continue
            
        x1, y1, x2, y2 = asset_boxes[mac]
        qty_val = asset.get("Quantity", "")
        
        # Colors & styling determination
        fill = asset_style["fill"]
        stroke = asset_style["stroke"]
        text_color = asset_style["text_color"]
        ip_color = asset_style["ip_color"]
        mac_color = asset_style["mac_color"]
        
        state_val = asset.get("State", "")
        has_state = False
        state_style = {}
        if state_val and state_val in states_config:
            has_state = True
            state_style = states_config[state_val]
            
        if static_lens_active:
            if has_state:
                fill = state_style.get("fill", fill)
                stroke = state_style.get("stroke", stroke)
                text_color = state_style.get("text_color", text_color)
                ip_color = state_style.get("ip_color", ip_color)
                mac_color = state_style.get("mac_color", mac_color)
            else:
                # Dim non-state assets in static PNG
                fill = "#f8fafc"
                stroke = "#cbd5e1"
                text_color = "#cbd5e1"
                ip_color = "#cbd5e1"
                mac_color = "#cbd5e1"
                
        # Interactive lens target attributes (HTML only)
        state_attrs = None
        ip_state_attrs = None
        mac_state_attrs = None
        if output_format == "html":
            if has_state:
                state_attrs = {
                    "data-state": state_val,
                    "data-default-fill": asset_style["fill"],
                    "data-state-fill": state_style.get("fill", asset_style["fill"]),
                    "data-default-stroke": asset_style["stroke"],
                    "data-state-stroke": state_style.get("stroke", asset_style["stroke"]),
                    "data-default-color": asset_style["text_color"],
                    "data-state-color": state_style.get("text_color", asset_style["text_color"])
                }
                ip_state_attrs = {
                    "data-state": state_val,
                    "data-default-color": asset_style["ip_color"],
                    "data-state-color": state_style.get("ip_color", state_style.get("text_color", asset_style["ip_color"]))
                }
                mac_state_attrs = {
                    "data-state": state_val,
                    "data-default-color": asset_style["mac_color"],
                    "data-state-color": state_style.get("mac_color", state_style.get("text_color", asset_style["mac_color"]))
                }
            else:
                state_attrs = {
                    "data-default-fill": asset_style["fill"],
                    "data-default-stroke": asset_style["stroke"],
                    "data-default-color": asset_style["text_color"]
                }
                ip_state_attrs = {
                    "data-default-color": asset_style["ip_color"]
                }
                mac_state_attrs = {
                    "data-default-color": asset_style["mac_color"]
                }

        # Draw box container(s)
        if qty_val:
            # Stacked rectangles (3 assets representation)
            for offset in [6, 3, 0]:
                canvas.draw_rect(
                    x1 - offset, y1 - offset, x2 - x1, y2 - y1,
                    fill_color=fill,
                    border_color=stroke,
                    border_width=1.5,
                    rx=4, ry=4,
                    layer="assets",
                    state_attrs=state_attrs
                )
        else:
            # Single standard rectangle
            canvas.draw_rect(
                x1, y1, x2 - x1, y2 - y1,
                fill_color=fill,
                border_color=stroke,
                border_width=1.5,
                rx=4, ry=4,
                layer="assets",
                state_attrs=state_attrs
            )
        
        # Write asset info
        # 1. Hostname (bold)
        canvas.draw_text(
            (x1 + x2)/2, y1 + 14,
            text=asset["Hostname"],
            font_size=10,
            color=text_color,
            align="center",
            bold=True,
            layer="assets",
            state_attrs=state_attrs
        )
        
        # 2. IP Address / Range (emphasized)
        ips_str = ", ".join(asset["IPs"])
        canvas.draw_text(
            (x1 + x2)/2, y1 + 28,
            text=ips_str,
            font_size=9,
            color=ip_color,
            align="center",
            bold=True,
            layer="assets",
            state_attrs=ip_state_attrs
        )
        
        # 3. MAC address or Quantity label
        third_line_text = f"Qty: {qty_val}" if qty_val else asset["MAC address"]
        canvas.draw_text(
            (x1 + x2)/2, y1 + 42,
            text=third_line_text,
            font_size=8,
            color=mac_color,
            align="center",
            layer="assets",
            state_attrs=mac_state_attrs
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
                color=mac_color,
                align="center",
                italic=True,
                layer="assets",
                state_attrs=mac_state_attrs
            )

    # 4. Render Flow Lines with Bridge Humps and Arrowheads
    flow_style = config.get("styles", {}).get("flow", {
        "stroke": "#d84315",
        "width": 1.5,
        "arrow_size": 6
    })
    
    rendered_flow_labels = []
    
    for f in flows:
        f_id = f["id"]
        path = flow_paths[f_id]
        
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
                
                # Check intersections specifically on this segment (using flow_id and segment index)
                seg_cross = crossings.get((f_id, i), [])
                
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
        longest_h_idx = -1
        max_h_len = -1
        for i in range(len(path) - 1):
            p1 = path[i]
            p2 = path[i+1]
            if p1[1] == p2[1]:
                length = abs(p2[0] - p1[0])
                if length > max_h_len:
                    max_h_len = length
                    longest_h = (p1, p2)
                    longest_h_idx = i
                    
        comment_text = f.get("comment", "")
        if comment_text:
            label_bg = bg_color
            if longest_h:
                p1, p2 = longest_h
                x_min = min(p1[0], p2[0])
                x_max = max(p1[0], p2[0])
                y = p1[1]
                
                # Gather all avoidance X coordinates within [x_min, x_max] to prevent label overlapping vertical lines/VLAN borders
                avoid_xs = []
                
                # 1. Vertical flow segments (any vertical segment that intersects the horizontal span)
                for v in vertical_segments:
                    if v["y1"] <= y <= v["y2"]:
                        vx = v["x"]
                        if x_min < vx < x_max:
                            avoid_xs.append(vx)
                            
                # 2. VLAN boundaries (any vertical boundary coordinate that intersects this range)
                for vlan, box in vlan_boxes.items():
                    vx1, vy1, vx2, vy2 = box
                    if vy1 <= y <= vy2:
                        if x_min < vx1 < x_max:
                            avoid_xs.append(vx1)
                        if x_min < vx2 < x_max:
                            avoid_xs.append(vx2)
                            
                # Deduplicate and sort
                avoid_xs = sorted(list(set(avoid_xs)))
                
                if not avoid_xs:
                    x_pos = (x_min + x_max) / 2
                else:
                    # Find the largest gap between avoidance points or segment endpoints
                    all_pts = [x_min] + avoid_xs + [x_max]
                    best_gap = -1
                    x_pos = (x_min + x_max) / 2
                    for idx in range(len(all_pts) - 1):
                        gap = all_pts[idx+1] - all_pts[idx]
                        if gap > best_gap:
                            best_gap = gap
                            x_pos = (all_pts[idx] + all_pts[idx+1]) / 2
                
                # Estimate text size and adjust position to avoid overlap with other flow labels
                tw = len(comment_text) * 4.4
                th = 12
                
                def has_overlap(x_test):
                    box_test = (x_test - tw/2 - 4, y - th/2 - 2, x_test + tw/2 + 4, y + th/2 + 2)
                    for r_box in rendered_flow_labels:
                        if not (box_test[2] <= r_box[0] or box_test[0] >= r_box[2] or
                                box_test[3] <= r_box[1] or box_test[1] >= r_box[3]):
                            return True
                    return False

                chosen_x = x_pos
                min_overlap_count = 99999
                best_x = x_pos
                
                max_shift = min(180, (x_max - x_min) / 2)
                shifts = [0]
                for step in range(1, 21):
                    shift_val = step * 12
                    if shift_val <= max_shift:
                        shifts.append(shift_val)
                        shifts.append(-shift_val)
                
                found_non_overlap = False
                for dx in shifts:
                    tx = x_pos + dx
                    if x_min + tw/2 + 5 <= tx <= x_max - tw/2 - 5:
                        if not has_overlap(tx):
                            chosen_x = tx
                            found_non_overlap = True
                            break
                        else:
                            box_test = (tx - tw/2 - 4, y - th/2 - 2, tx + tw/2 + 4, y + th/2 + 2)
                            overlaps_cnt = 0
                            for r_box in rendered_flow_labels:
                                if not (box_test[2] <= r_box[0] or box_test[0] >= r_box[2] or
                                        box_test[3] <= r_box[1] or box_test[1] >= r_box[3]):
                                    overlaps_cnt += 1
                            if overlaps_cnt < min_overlap_count:
                                min_overlap_count = overlaps_cnt
                                best_x = tx
                                
                if not found_non_overlap:
                    chosen_x = best_x
                
                rendered_flow_labels.append((chosen_x - tw/2 - 4, y - th/2 - 2, chosen_x + tw/2 + 4, y + th/2 + 2))
                x_pos = chosen_x

                for z, (y_start, y_end) in zone_y_ranges.items():
                    if y_start <= y <= y_end:
                        label_bg = config.get("zones", {}).get(z, {}).get("fill", bg_color)
                        if label_bg == "none":
                            label_bg = bg_color
                        break
                # Render centered on the horizontal line
                canvas.draw_text(
                    x_pos, y,
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

    # 5. Render Zone Labels (drawn last so they are on top of flow lines for masking)
    zones_styles = config.get("zones", {})
    for zone in zone_order:
        y_start, y_end = zone_y_ranges[zone]
        style = zones_styles.get(zone, {
            "fill": "#fafafa",
            "stroke": "#d1d5db",
            "text_color": "#1f2937",
            "label": zone
        })
        
        # Use zone fill color as background color for text halo masking
        label_bg = style["fill"]
        if label_bg == "none":
            label_bg = bg_color
            
        canvas.draw_text(
            120, y_start + 18,
            text=style.get("label", zone),
            font_size=13,
            color=style["text_color"],
            align="left",
            bold=True,
            bg_color=label_bg,
            bg_style="halo",
            layer="labels"
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
