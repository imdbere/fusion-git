"""Build the add-in's toolbar icons from the SVG sources in tools/icons.

Each source is drawn once on a 32-unit grid in Fusion's light-theme palette. Fusion
looks icons up by file name, so every icon is written as 16x16/24x24/32x32.svg plus a
-dark variant. Dark variants follow Fusion's own icons: outlines (#666666 strokes)
are dropped, #666666 line art turns light grey, accent colours get brighter.
"""

import os
import re

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
SOURCES = os.path.join(ROOT, "tools", "icons")
# Fusion caches icons by folder path until it restarts; use a new folder when the icons change.
OUT = os.path.join(ROOT, "FusionGit", "resources", "svg-icons")
SIZES = (16, 24, 32)
OUTLINE = "#666666"

DARK_COLORS = {
    "#666666": "#D9D9D9",
    "#178FE6": "#73C5FF",
    "#67B1E6": "#89CBFA",
    "#24B353": "#62D987",
    "#E67E35": "#F7B688",
}


def themed(svg, dark):
    if not dark:
        return svg
    svg = re.sub(rf'\s*stroke="{OUTLINE}"\s*stroke-width="[\d.]+"', "", svg, flags=re.IGNORECASE)
    for light, dark_color in DARK_COLORS.items():
        svg = re.sub(re.escape(light), dark_color, svg, flags=re.IGNORECASE)
    return svg


def sized(svg, size):
    svg = re.sub(r'(<svg[^>]*?)width="\d+"', rf'\g<1>width="{size}"', svg, count=1)
    svg = re.sub(r'(<svg[^>]*?)height="\d+"', rf'\g<1>height="{size}"', svg, count=1)
    # Keep outlines exactly one device pixel wide at every size.
    return re.sub(rf'(stroke="{OUTLINE}"\s*stroke-width=")[\d.]+"', rf'\g<1>{32 / size:g}"', svg,
                  flags=re.IGNORECASE)


def main():
    names = sorted(f[:-4] for f in os.listdir(SOURCES) if f.endswith(".svg"))
    for name in names:
        with open(os.path.join(SOURCES, f"{name}.svg"), encoding="utf-8") as f:
            source = f.read()
        folder = os.path.join(OUT, name)
        os.makedirs(folder, exist_ok=True)
        for size in SIZES:
            for dark in (False, True):
                file_name = f"{size}x{size}{'-dark' if dark else ''}.svg"
                with open(os.path.join(folder, file_name), "w", encoding="utf-8") as f:
                    f.write(themed(sized(source, size), dark))
    print(f"built {len(names)} icons in {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main()
