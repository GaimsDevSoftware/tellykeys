#!/usr/bin/env python3
"""Velvet Signal — TellyKeys "after dark" identity plate.
A sensual neon-noir companion to the cool Quiet Transmission plate."""
import math
import cairosvg

W, H = 1500, 2100

# --- palette (Velvet Signal) ---
BG      = "#150A10"
BG2     = "#23101A"
HAIR    = "#3C2731"
GOLD    = "#B98A66"
GOLD_DK = "#6E4F42"
EMBER1  = "#FF4D6D"
EMBER2  = "#FF8A5B"
EMBER_HI= "#FFCBB0"
ROSE    = "#EA6486"
CHAMP   = "#F3E5DC"
MAUVE   = "#A57E8C"
MAUVE2  = "#6B5160"

F_WORD = "Gloock"
F_ITAL = "Instrument Serif"
F_MONO = "DM Mono"
F_GEO  = "Geist Mono"

cx = W / 2
HC = (cx, 720)

P = []
def add(s): P.append(s)

# ---------- defs ----------
add(f'''<defs>
  <radialGradient id="emberCore" cx="50%" cy="50%" r="50%">
    <stop offset="0%" stop-color="{EMBER_HI}" stop-opacity="0.92"/>
    <stop offset="30%" stop-color="{EMBER1}" stop-opacity="0.62"/>
    <stop offset="100%" stop-color="{EMBER1}" stop-opacity="0"/>
  </radialGradient>
  <radialGradient id="emberBloom" cx="50%" cy="50%" r="50%">
    <stop offset="0%" stop-color="{EMBER2}" stop-opacity="0.46"/>
    <stop offset="45%" stop-color="{EMBER1}" stop-opacity="0.18"/>
    <stop offset="100%" stop-color="{EMBER1}" stop-opacity="0"/>
  </radialGradient>
  <radialGradient id="vignette" cx="50%" cy="40%" r="68%">
    <stop offset="0%" stop-color="{BG2}" stop-opacity="0.9"/>
    <stop offset="62%" stop-color="{BG}" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="curve" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="{EMBER2}"/>
    <stop offset="50%" stop-color="{EMBER1}"/>
    <stop offset="100%" stop-color="{ROSE}"/>
  </linearGradient>
  <linearGradient id="word" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="{CHAMP}"/>
    <stop offset="100%" stop-color="{GOLD}"/>
  </linearGradient>
</defs>''')

# ---------- background ----------
add(f'<rect width="{W}" height="{H}" fill="{BG}"/>')
add(f'<rect width="{W}" height="{H}" fill="url(#vignette)"/>')

# faint warm dot grid
dots = []
gy = 84
while gy < H - 70:
    gx = 84
    while gx < W - 70:
        dots.append(f'<circle cx="{gx}" cy="{gy}" r="1.1"/>')
        gx += 48
    gy += 48
add(f'<g fill="{ROSE}" opacity="0.04">{"".join(dots)}</g>')

# ---------- frame + registration ----------
M = 64
add(f'<rect x="{M}" y="{M}" width="{W-2*M}" height="{H-2*M}" fill="none" stroke="{HAIR}" stroke-width="1.3"/>')
def reg(x, y):
    return (f'<g stroke="{GOLD_DK}" stroke-width="1.2">'
            f'<line x1="{x-11}" y1="{y}" x2="{x+11}" y2="{y}"/>'
            f'<line x1="{x}" y1="{y-11}" x2="{x}" y2="{y+11}"/></g>')
for rx in (M+30, W-M-30):
    for ry in (M+30, H-M-30):
        add(reg(rx, ry))

x0, x1 = 130, W-130

# ---------- header ----------
add(f'<text x="{x0}" y="150" font-family="{F_MONO}" font-size="27" fill="{CHAMP}" letter-spacing="9">TELLYKEYS</text>')
add(f'<text x="{x0+2}" y="178" font-family="{F_GEO}" font-size="12.5" fill="{MAUVE}" letter-spacing="6">VELVET SIGNAL — AFTER DARK</text>')
add(f'<text x="{x1}" y="138" text-anchor="end" font-family="{F_GEO}" font-size="12.5" fill="{MAUVE}" letter-spacing="3">PLATE 02 — II</text>')
add(f'<text x="{x1}" y="161" text-anchor="end" font-family="{F_GEO}" font-size="12.5" fill="{MAUVE}" letter-spacing="3">37.0 °C</text>')
add(f'<text x="{x1}" y="184" text-anchor="end" font-family="{F_GEO}" font-size="12.5" fill="{MAUVE}" letter-spacing="3">λ  940 nm</text>')
add(f'<line x1="{x0}" y1="206" x2="{x1}" y2="206" stroke="{HAIR}" stroke-width="1.2"/>')

# ---------- left ruler ----------
ticks = []
y = 286; i = 0
while y <= 1158:
    major = (i % 5 == 0)
    ln = 17 if major else 8
    ticks.append(f'<line x1="100" y1="{y}" x2="{100+ln}" y2="{y}"/>')
    if major:
        ticks.append(f'<text x="93" y="{y+4}" text-anchor="end" font-family="{F_GEO}" font-size="10" fill="{MAUVE2}">{i*10:03d}</text>')
    y += 20; i += 1
add(f'<g stroke="{MAUVE2}" stroke-width="1.1" opacity="0.85">{"".join(ticks)}</g>')

# ---------- hero ----------
hx, hy = HC
# bloom
add(f'<circle cx="{hx}" cy="{hy}" r="300" fill="url(#emberBloom)"/>')
# concentric heat rings
for r, w, op in [(150,2.0,0.85),(225,1.7,0.5),(305,1.4,0.32),(390,1.2,0.19),(480,1.0,0.10)]:
    add(f'<circle cx="{hx}" cy="{hy}" r="{r}" fill="none" stroke="{EMBER1}" stroke-width="{w}" opacity="{op}"/>')
# subtle rose-gold cardinal ticks on inner ring (the quiet directional nod)
for ang in (0,90,180,270):
    a = math.radians(ang)
    add(f'<line x1="{hx+150*math.cos(a):.1f}" y1="{hy+150*math.sin(a):.1f}" '
        f'x2="{hx+168*math.cos(a):.1f}" y2="{hy+168*math.sin(a):.1f}" stroke="{GOLD}" stroke-width="1.5" opacity="0.8"/>')

# sinuous velvet curve threading the core (glow + bright)
curve = (f'M {hx},{hy-360} C {hx-150},{hy-205} {hx+150},{hy-120} {hx},{hy} '
         f'C {hx-150},{hy+120} {hx+150},{hy+205} {hx},{hy+360}')
add(f'<path d="{curve}" fill="none" stroke="{EMBER1}" stroke-width="11" opacity="0.16" stroke-linecap="round"/>')
add(f'<path d="{curve}" fill="none" stroke="url(#curve)" stroke-width="2.2" opacity="0.95" stroke-linecap="round"/>')

# molten core
add(f'<circle cx="{hx}" cy="{hy}" r="128" fill="url(#emberCore)"/>')
add(f'<circle cx="{hx}" cy="{hy}" r="18" fill="{EMBER1}"/>')
add(f'<circle cx="{hx}" cy="{hy}" r="18" fill="none" stroke="{EMBER_HI}" stroke-width="1.2"/>')
add(f'<circle cx="{hx-5}" cy="{hy-5}" r="3.4" fill="{EMBER_HI}" opacity="0.9"/>')

# quiet annotation
add(f'<text x="{hx}" y="{hy+340}" text-anchor="middle" font-family="{F_GEO}" font-size="12" '
    f'fill="{MAUVE2}" letter-spacing="4">PROXIMITY · 0.0 m</text>')

# ---------- wordmark ----------
add(f'<text x="{cx}" y="1342" text-anchor="middle" font-family="{F_WORD}" font-size="138" '
    f'fill="url(#word)" letter-spacing="3">TellyKeys</text>')

# tagline
tag_y = 1398
add(f'<text x="{cx}" y="{tag_y}" text-anchor="middle" font-family="{F_MONO}" font-size="16" '
    f'fill="{MAUVE}" letter-spacing="12">GOOGLE  TV  REMOTE</text>')
add(f'<line x1="{cx-320}" y1="{tag_y-6}" x2="{cx-195}" y2="{tag_y-6}" stroke="{GOLD_DK}" stroke-width="1.2"/>')
add(f'<line x1="{cx+195}" y1="{tag_y-6}" x2="{cx+320}" y2="{tag_y-6}" stroke="{GOLD_DK}" stroke-width="1.2"/>')

# ---------- lower band ----------
add(f'<line x1="{x0}" y1="1470" x2="{x1}" y2="1470" stroke="{HAIR}" stroke-width="1.2"/>')

# languid pulse waveform
wy = 1552; amp, cyc, half = 26, 3, 280
pts = []; n = 240
for k in range(n+1):
    xx = cx - half + (2*half)*k/n
    yy = wy + amp*math.sin(2*math.pi*cyc*k/n)
    pts.append(("M" if k == 0 else "L") + f"{xx:.1f},{yy:.1f}")
add(f'<path d="{" ".join(pts)}" fill="none" stroke="{EMBER1}" stroke-width="6" opacity="0.16" stroke-linecap="round"/>')
add(f'<path d="{" ".join(pts)}" fill="none" stroke="url(#curve)" stroke-width="1.8" opacity="0.9"/>')
add(f'<text x="{cx-half-22}" y="{wy+4}" text-anchor="end" font-family="{F_GEO}" font-size="12" fill="{MAUVE}" letter-spacing="3">PULSE</text>')
add(f'<text x="{cx+half+22}" y="{wy+4}" font-family="{F_GEO}" font-size="12" fill="{MAUVE}" letter-spacing="3">72 BPM</text>')

# swatches
sw = 96
swatches = [("MIDNIGHT", BG), ("ROSE", EMBER1), ("AMBER", EMBER2), ("CHAMPAGNE", CHAMP)]
step = 196
start = cx - (step*(len(swatches)-1))/2 - sw/2
top = 1656
for idx, (name, col) in enumerate(swatches):
    sx = start + idx*step
    add(f'<rect x="{sx:.1f}" y="{top}" width="{sw}" height="{sw}" rx="9" fill="{col}" stroke="{HAIR}" stroke-width="1.2"/>')
    midx = sx + sw/2
    add(f'<text x="{midx:.1f}" y="{top+sw+30}" text-anchor="middle" font-family="{F_MONO}" font-size="12.5" fill="{CHAMP}" letter-spacing="1.5">{name}</text>')
    add(f'<text x="{midx:.1f}" y="{top+sw+50}" text-anchor="middle" font-family="{F_GEO}" font-size="11.5" fill="{MAUVE}" letter-spacing="1">{col.upper()}</text>')

# ---------- footer ----------
add(f'<line x1="{x0}" y1="1892" x2="{x1}" y2="1892" stroke="{HAIR}" stroke-width="1.2"/>')
add(f'<text x="{x0}" y="1956" font-family="{F_ITAL}" font-style="italic" font-size="40" fill="{CHAMP}">Turn me on<tspan fill="{EMBER1}">.</tspan></text>')
add(f'<text x="{x1}" y="1944" text-anchor="end" font-family="{F_GEO}" font-size="11.5" fill="{MAUVE2}" letter-spacing="3">TELLYKEYS · VELVET SIGNAL</text>')
add(f'<text x="{x1}" y="1964" text-anchor="end" font-family="{F_GEO}" font-size="11.5" fill="{MAUVE2}" letter-spacing="3">AFTER DARK · MMXXVI</text>')

svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">{"".join(P)}</svg>'
with open("design/tellykeys-velvet.svg", "w") as f:
    f.write(svg)
cairosvg.svg2png(bytestring=svg.encode(), write_to="design/tellykeys-velvet.png",
                 output_width=W*2, output_height=H*2)
print("rendered design/tellykeys-velvet.png")
