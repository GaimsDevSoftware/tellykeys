#!/usr/bin/env python3
"""Quiet Transmission — TellyKeys visual identity plate.
Generates an SVG and renders a high-resolution PNG via cairosvg."""
import math
import cairosvg

W, H = 1500, 2100

# --- palette (Quiet Transmission) ---
INK      = "#0C0F16"
INK2     = "#11151F"
HAIR     = "#232B3B"
HAIR_SOFT= "#1A2030"
ACCENT   = "#4D7CFF"
ACCENT_HI= "#86A8FF"
LIGHT    = "#EEF1F8"
MUTE     = "#7B859B"
MUTE2    = "#4E586E"

# fonts
F_WORD = "Jura Medium"
F_THIN = "Jura Light"
F_MONO = "DM Mono"
F_GEO  = "Geist Mono"

cx = W / 2
HC = (cx, 700)   # hero center

P = []  # svg pieces
def add(s): P.append(s)

# ---------- defs ----------
add(f'''<defs>
  <radialGradient id="coreGlow" cx="50%" cy="50%" r="50%">
    <stop offset="0%" stop-color="{ACCENT_HI}" stop-opacity="0.95"/>
    <stop offset="35%" stop-color="{ACCENT}" stop-opacity="0.55"/>
    <stop offset="100%" stop-color="{ACCENT}" stop-opacity="0"/>
  </radialGradient>
  <radialGradient id="vignette" cx="50%" cy="42%" r="62%">
    <stop offset="0%" stop-color="#121826" stop-opacity="0.55"/>
    <stop offset="60%" stop-color="{INK}" stop-opacity="0"/>
  </radialGradient>
</defs>''')

# ---------- background ----------
add(f'<rect x="0" y="0" width="{W}" height="{H}" fill="{INK}"/>')
add(f'<rect x="0" y="0" width="{W}" height="{H}" fill="url(#vignette)"/>')

# faint dot grid
dots = []
gy = 84
while gy < H - 70:
    gx = 84
    while gx < W - 70:
        dots.append(f'<circle cx="{gx}" cy="{gy}" r="1.1"/>')
        gx += 48
    gy += 48
add(f'<g fill="{LIGHT}" opacity="0.035">{"".join(dots)}</g>')

# ---------- frame + registration ----------
M = 64
add(f'<rect x="{M}" y="{M}" width="{W-2*M}" height="{H-2*M}" fill="none" stroke="{HAIR}" stroke-width="1.3"/>')
def reg(x, y):
    return (f'<g stroke="{MUTE2}" stroke-width="1.2">'
            f'<line x1="{x-11}" y1="{y}" x2="{x+11}" y2="{y}"/>'
            f'<line x1="{x}" y1="{y-11}" x2="{x}" y2="{y+11}"/></g>')
for rx in (M+30, W-M-30):
    for ry in (M+30, H-M-30):
        add(reg(rx, ry))

x0, x1 = 130, W-130   # content margins

# ---------- header ----------
add(f'<text x="{x0}" y="150" font-family="{F_MONO}" font-size="27" fill="{LIGHT}" letter-spacing="9">TELLYKEYS</text>')
add(f'<text x="{x0+2}" y="178" font-family="{F_GEO}" font-size="12.5" fill="{MUTE}" letter-spacing="6">VISUAL  SIGNAL  SYSTEM</text>')
add(f'<text x="{x1}" y="138" text-anchor="end" font-family="{F_GEO}" font-size="12.5" fill="{MUTE}" letter-spacing="3">PLATE 01 — VI</text>')
add(f'<text x="{x1}" y="161" text-anchor="end" font-family="{F_GEO}" font-size="12.5" fill="{MUTE}" letter-spacing="3">λ  940 nm</text>')
add(f'<text x="{x1}" y="184" text-anchor="end" font-family="{F_GEO}" font-size="12.5" fill="{MUTE}" letter-spacing="3">ƒ  38 kHz</text>')
add(f'<line x1="{x0}" y1="206" x2="{x1}" y2="206" stroke="{HAIR}" stroke-width="1.2"/>')

# ---------- left ruler ----------
ruler_x = 100
ticks = []
y = 286
i = 0
while y <= 1158:
    major = (i % 5 == 0)
    ln = 17 if major else 8
    ticks.append(f'<line x1="{ruler_x}" y1="{y}" x2="{ruler_x+ln}" y2="{y}"/>')
    if major:
        ticks.append(f'<text x="{ruler_x-7}" y="{y+4}" text-anchor="end" font-family="{F_GEO}" '
                     f'font-size="10" fill="{MUTE2}">{i*10:03d}</text>')
    y += 20
    i += 1
add(f'<g stroke="{MUTE2}" stroke-width="1.1" opacity="0.85">{"".join(ticks)}</g>')

# ---------- hero ----------
hx, hy = HC
# soft luminous bloom binding the core to the signal field
add(f'<circle cx="{hx}" cy="{hy}" r="230" fill="url(#coreGlow)" opacity="0.16"/>')
# signal rings (faint, behind)
for r, w, op in [(240,1.5,0.52),(312,1.35,0.35),(388,1.2,0.22),(470,1.05,0.13)]:
    add(f'<circle cx="{hx}" cy="{hy}" r="{r}" fill="none" stroke="{ACCENT}" stroke-width="{w}" opacity="{op}"/>')
# cardinal pips on first ring (with faint halo)
for ang in (0,90,180,270):
    a = math.radians(ang)
    px = hx + 240*math.cos(a); py = hy + 240*math.sin(a)
    add(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="7" fill="{ACCENT}" opacity="0.16"/>')
    add(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.2" fill="{ACCENT}" opacity="0.7"/>')

# nav disc
add(f'<circle cx="{hx}" cy="{hy}" r="150" fill="{INK}" stroke="{LIGHT}" stroke-width="2.4"/>')
add(f'<circle cx="{hx}" cy="{hy}" r="133" fill="none" stroke="{HAIR}" stroke-width="1.1"/>')
# machined dial notches on the diagonals (echo of the margin ruler)
for ang in (45,135,225,315):
    a = math.radians(ang)
    add(f'<line x1="{hx+150*math.cos(a):.1f}" y1="{hy+150*math.sin(a):.1f}" '
        f'x2="{hx+136*math.cos(a):.1f}" y2="{hy+136*math.sin(a):.1f}" stroke="{MUTE}" stroke-width="1.4"/>')

# core glow
add(f'<circle cx="{hx}" cy="{hy}" r="118" fill="url(#coreGlow)" opacity="0.8"/>')

# directional chevrons
chev = f'M {hx-21},{hy-70} L {hx},{hy-104} L {hx+21},{hy-70}'
for ang in (0,90,180,270):
    add(f'<path d="{chev}" fill="none" stroke="{LIGHT}" stroke-width="2.4" '
        f'stroke-linecap="round" stroke-linejoin="round" transform="rotate({ang} {hx} {hy})"/>')

# core
add(f'<circle cx="{hx}" cy="{hy}" r="25" fill="{ACCENT}"/>')
add(f'<circle cx="{hx}" cy="{hy}" r="25" fill="none" stroke="{ACCENT_HI}" stroke-width="1.6"/>')
add(f'<circle cx="{hx-7}" cy="{hy-7}" r="4.5" fill="{LIGHT}" opacity="0.85"/>')

# keycode annotations (with ink halos)
def halo_label(tx, ty, text, anchor="middle", wpad=None):
    w = wpad if wpad else (len(text)*8.6 + 22)
    if anchor == "middle":
        rx = tx - w/2
    elif anchor == "end":
        rx = tx - w + 11
    else:
        rx = tx - 11
    add(f'<rect x="{rx:.1f}" y="{ty-17:.1f}" width="{w:.1f}" height="25" rx="5" fill="{INK}" opacity="0.72"/>')
    add(f'<text x="{tx}" y="{ty}" text-anchor="{anchor}" font-family="{F_MONO}" '
        f'font-size="13.5" fill="{MUTE}" letter-spacing="2">{text}</text>')

# leader ticks from disc edge outward along axes
for ang, lx, ly, label, anch in [
    (270, hx,      hy-205, "DPAD_UP",    "middle"),
    (0,   hx+210,  hy+5,   "DPAD_RIGHT", "start"),
    (90,  hx,      hy+213, "DPAD_DOWN",  "middle"),
    (180, hx-210,  hy+5,   "DPAD_LEFT",  "end"),
]:
    a = math.radians(ang)
    add(f'<line x1="{hx+155*math.cos(a):.1f}" y1="{hy+155*math.sin(a):.1f}" '
        f'x2="{hx+186*math.cos(a):.1f}" y2="{hy+186*math.sin(a):.1f}" stroke="{MUTE2}" stroke-width="1.1"/>')
    halo_label(lx, ly, label, anch)

# center callout
add(f'<text x="{hx}" y="{hy+330}" text-anchor="middle" font-family="{F_GEO}" font-size="12" '
    f'fill="{MUTE2}" letter-spacing="4">DPAD_CENTER · OK</text>')

# ---------- wordmark ----------
add(f'<text x="{cx}" y="1336" text-anchor="middle" font-family="{F_WORD}" font-size="118" '
    f'fill="{LIGHT}" letter-spacing="2.5">TellyKeys</text>')

# tagline with flanking rules
tag_y = 1392
add(f'<text x="{cx}" y="{tag_y}" text-anchor="middle" font-family="{F_MONO}" font-size="17.5" '
    f'fill="{MUTE}" letter-spacing="11">GOOGLE  TV  REMOTE</text>')
add(f'<line x1="{cx-330}" y1="{tag_y-6}" x2="{cx-200}" y2="{tag_y-6}" stroke="{HAIR}" stroke-width="1.2"/>')
add(f'<line x1="{cx+200}" y1="{tag_y-6}" x2="{cx+330}" y2="{tag_y-6}" stroke="{HAIR}" stroke-width="1.2"/>')

# ---------- lower band ----------
add(f'<line x1="{x0}" y1="1466" x2="{x1}" y2="1466" stroke="{HAIR}" stroke-width="1.2"/>')

# carrier waveform readout
wy = 1546
amp, cyc, half = 17, 6, 270
pts = []
n = 240
for k in range(n+1):
    xx = cx - half + (2*half)*k/n
    yy = wy + amp*math.sin(2*math.pi*cyc*k/n)
    pts.append(("M" if k == 0 else "L") + f"{xx:.1f},{yy:.1f}")
add(f'<path d="{" ".join(pts)}" fill="none" stroke="{ACCENT}" stroke-width="1.5" opacity="0.75"/>')
add(f'<text x="{cx-half-22}" y="{wy+4}" text-anchor="end" font-family="{F_GEO}" font-size="12" fill="{MUTE}" letter-spacing="3">CARRIER</text>')
add(f'<text x="{cx+half+22}" y="{wy+4}" font-family="{F_GEO}" font-size="12" fill="{MUTE}" letter-spacing="3">38 kHz</text>')

# palette swatches
sw = 96
swatches = [("VOID", INK), ("SIGNAL", ACCENT), ("LUMEN", LIGHT), ("GRAPHITE", MUTE)]
step = 196
start = cx - (step*(len(swatches)-1))/2 - sw/2
top = 1648
for idx, (name, col) in enumerate(swatches):
    sx = start + idx*step
    add(f'<rect x="{sx:.1f}" y="{top}" width="{sw}" height="{sw}" rx="9" fill="{col}" stroke="{HAIR}" stroke-width="1.2"/>')
    midx = sx + sw/2
    add(f'<text x="{midx:.1f}" y="{top+sw+30}" text-anchor="middle" font-family="{F_MONO}" font-size="13" fill="{LIGHT}" letter-spacing="2">{name}</text>')
    add(f'<text x="{midx:.1f}" y="{top+sw+50}" text-anchor="middle" font-family="{F_GEO}" font-size="11.5" fill="{MUTE}" letter-spacing="1">{col.upper()}</text>')

# ---------- footer ----------
add(f'<line x1="{x0}" y1="1884" x2="{x1}" y2="1884" stroke="{HAIR}" stroke-width="1.2"/>')
add(f'<text x="{x0}" y="1948" font-family="{F_THIN}" font-size="27" fill="{LIGHT}" letter-spacing="1">Command, at a distance<tspan fill="{ACCENT}">.</tspan></text>')
add(f'<text x="{x1}" y="1942" text-anchor="end" font-family="{F_GEO}" font-size="11.5" fill="{MUTE2}" letter-spacing="3">TELLYKEYS · VISUAL SYSTEM</text>')
add(f'<text x="{x1}" y="1962" text-anchor="end" font-family="{F_GEO}" font-size="11.5" fill="{MUTE2}" letter-spacing="3">EDITION MMXXVI</text>')

svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">{"".join(P)}</svg>'

with open("design/tellykeys-identity.svg", "w") as f:
    f.write(svg)

cairosvg.svg2png(bytestring=svg.encode(), write_to="design/tellykeys-identity.png",
                 output_width=W*2, output_height=H*2)
print("rendered design/tellykeys-identity.png", W*2, "x", H*2)
