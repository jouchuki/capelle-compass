#!/usr/bin/env python3
"""Rasterize Capelle aan den IJssel's 9 official CBS wijken (2024) onto a
coarse pixel grid for the frontend's hero map.

Source : PDOK OGC API — CBS Wijken en Buurten 2024 (WGS84 / EPSG:4326).
Output : frontend/src/data/capelleWijken.ts  (TypeScript module).

Run once after fetching the source JSON; commit the generated TS file.

Usage:
    curl -G "https://api.pdok.nl/cbs/wijken-en-buurten-2024/ogc/v1/collections/wijken/items" \\
        --data-urlencode "f=json" \\
        --data-urlencode "bbox=4.55,51.91,4.67,51.98" \\
        --data-urlencode "bbox-crs=http://www.opengis.net/def/crs/OGC/1.3/CRS84" \\
        --data-urlencode "limit=50" -o /tmp/capelle_wijken.json
    python3 scripts/build_capelle_grid.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

SOURCE = Path('/tmp/capelle_wijken.json')
OUTPUT = Path(__file__).resolve().parents[1] / 'frontend' / 'src' / 'data' / 'capelleWijken.ts'

GEMEENTE_CODE = 'GM0502'

# Grid resolution. Aspect ratio is chosen so cells are roughly square
# given Capelle's bbox at ~52°N. Capelle is ~3.8 km wide × ~3.2 km tall.
COLS = 40
ROWS = 30

# Stable wijk slugs used by the frontend. Order = render priority when
# multiple polygons claim the same cell (later wins on tie).
WIJK_SLUGS: dict[str, str] = {
    'WK050201': 'capelle-west-sgravenland',
    'WK050202': 'middelwatering-west',
    'WK050203': 'middelwatering-oost',
    'WK050204': 'oostgaarde-zuid',
    'WK050205': 'oostgaarde-noord',
    'WK050206': 'schenkel',
    'WK050207': 'schollevaar-zuid',
    'WK050208': 'schollevaar-noord',
    'WK050209': 'rivium-fascinatio',
}

# Display labels (Dutch).
WIJK_LABELS: dict[str, str] = {
    'capelle-west-sgravenland': "Capelle-West & 's-Gravenland",
    'middelwatering-west': 'Middelwatering-West',
    'middelwatering-oost': 'Middelwatering-Oost',
    'oostgaarde-zuid': 'Oostgaarde-Zuid',
    'oostgaarde-noord': 'Oostgaarde-Noord',
    'schenkel': 'Schenkel',
    'schollevaar-zuid': 'Schollevaar-Zuid',
    'schollevaar-noord': 'Schollevaar-Noord',
    'rivium-fascinatio': 'Rivium & Fascinatio',
}


def point_in_ring(x: float, y: float, ring: list[list[float]]) -> bool:
    """Standard ray-casting point-in-polygon test for a single ring."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        intersect = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersect:
            inside = not inside
        j = i
    return inside


def point_in_multipolygon(x: float, y: float, geometry: dict) -> bool:
    """Test point against a GeoJSON Polygon or MultiPolygon (outer rings,
    minus holes)."""
    gtype = geometry['type']
    if gtype == 'Polygon':
        polys = [geometry['coordinates']]
    elif gtype == 'MultiPolygon':
        polys = geometry['coordinates']
    else:
        return False
    for poly in polys:
        if not poly:
            continue
        outer = poly[0]
        if not point_in_ring(x, y, outer):
            continue
        # Subtract holes.
        in_hole = False
        for hole in poly[1:]:
            if point_in_ring(x, y, hole):
                in_hole = True
                break
        if not in_hole:
            return True
    return False


def polygon_centroid_lonlat(geometry: dict) -> tuple[float, float]:
    """Crude area-weighted centroid across all outer rings of a Polygon/
    MultiPolygon. Good enough for chip-anchoring; not survey-grade."""
    polys = (
        [geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']
    )
    total_area = 0.0
    cx = 0.0
    cy = 0.0
    for poly in polys:
        if not poly:
            continue
        ring = poly[0]
        a = 0.0
        rx = 0.0
        ry = 0.0
        n = len(ring)
        for i in range(n):
            x0, y0 = ring[i][0], ring[i][1]
            x1, y1 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
            cross = x0 * y1 - x1 * y0
            a += cross
            rx += (x0 + x1) * cross
            ry += (y0 + y1) * cross
        a *= 0.5
        if a == 0:
            continue
        rx /= 6 * a
        ry /= 6 * a
        total_area += abs(a)
        cx += rx * abs(a)
        cy += ry * abs(a)
    return (cx / total_area, cy / total_area) if total_area else (0.0, 0.0)


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(
            f'Source file {SOURCE} not found. Fetch it first; see module docstring.'
        )
    payload = json.loads(SOURCE.read_text())
    wijken = [
        f
        for f in payload.get('features', [])
        if f.get('properties', {}).get('gemeentecode') == GEMEENTE_CODE
    ]
    if len(wijken) != 9:
        raise SystemExit(f'Expected 9 wijken, got {len(wijken)}.')

    # Compute the tight bbox of all 9 polygons in lon/lat.
    min_x = min_y = math.inf
    max_x = max_y = -math.inf
    for f in wijken:
        for poly in (
            [f['geometry']['coordinates']]
            if f['geometry']['type'] == 'Polygon'
            else f['geometry']['coordinates']
        ):
            for ring in poly:
                for x, y in ring:
                    min_x = min(min_x, x)
                    min_y = min(min_y, y)
                    max_x = max(max_x, x)
                    max_y = max(max_y, y)

    # Tiny outward pad so we don't lose edges to cell-center rounding.
    pad_x = (max_x - min_x) * 0.005
    pad_y = (max_y - min_y) * 0.005
    min_x -= pad_x
    max_x += pad_x
    min_y -= pad_y
    max_y += pad_y

    cell_w = (max_x - min_x) / COLS
    cell_h = (max_y - min_y) / ROWS

    # Rasterize.
    grid: list[list[str]] = [['empty'] * COLS for _ in range(ROWS)]
    for row in range(ROWS):
        # Row 0 is north (top). lat decreases as row increases.
        y = max_y - (row + 0.5) * cell_h
        for col in range(COLS):
            x = min_x + (col + 0.5) * cell_w
            for f in wijken:
                code = f['properties'].get('wijkcode', '')
                slug = WIJK_SLUGS.get(code)
                if not slug:
                    continue
                if point_in_multipolygon(x, y, f['geometry']):
                    grid[row][col] = slug
                    break

    # Centroids → grid coords.
    centroids: list[dict] = []
    for f in wijken:
        code = f['properties'].get('wijkcode', '')
        slug = WIJK_SLUGS.get(code)
        if not slug:
            continue
        cx, cy = polygon_centroid_lonlat(f['geometry'])
        col = (cx - min_x) / cell_w
        row = (max_y - cy) / cell_h
        centroids.append(
            {
                'slug': slug,
                'wijkcode': code,
                'label': WIJK_LABELS[slug],
                'population': int(f['properties'].get('aantal_inwoners') or 0),
                'col': round(col, 2),
                'row': round(row, 2),
            }
        )

    # Stats — useful for spotting layout issues.
    counts: dict[str, int] = {}
    for r in grid:
        for cell in r:
            counts[cell] = counts.get(cell, 0) + 1

    # Emit TypeScript.
    lines: list[str] = []
    lines.append('// AUTO-GENERATED by scripts/build_capelle_grid.py — do not edit by hand.')
    lines.append(
        '// Source: PDOK OGC API — CBS Wijken en Buurten 2024 (gemeente GM0502 — Capelle aan den IJssel).'
    )
    lines.append(f'// Grid: {COLS} cols × {ROWS} rows. Cell (0,0) is the NW corner.')
    lines.append('')
    lines.append(
        "export type WijkSlug =\n  "
        + '\n  | '.join(f"'{slug}'" for slug in WIJK_LABELS)
        + ';'
    )
    lines.append('')
    lines.append("export type Cell = WijkSlug | 'empty';")
    lines.append('')
    lines.append(f'export const COLS = {COLS};')
    lines.append(f'export const ROWS = {ROWS};')
    lines.append('')
    lines.append('export interface WijkMeta {')
    lines.append('  slug: WijkSlug;')
    lines.append('  wijkcode: string;')
    lines.append('  label: string;')
    lines.append('  population: number;')
    lines.append('  /** Approximate column (0..COLS-1) of the wijk centroid. */')
    lines.append('  col: number;')
    lines.append('  /** Approximate row (0..ROWS-1) of the wijk centroid. */')
    lines.append('  row: number;')
    lines.append('}')
    lines.append('')
    lines.append('export const WIJKEN: readonly WijkMeta[] = [')
    for c in centroids:
        slug = json.dumps(c['slug'])
        wijkcode = json.dumps(c['wijkcode'])
        label = json.dumps(c['label'])
        lines.append(
            f'  {{ slug: {slug}, wijkcode: {wijkcode}, label: {label}, '
            f"population: {c['population']}, col: {c['col']}, row: {c['row']} }},"
        )
    lines.append('];')
    lines.append('')
    lines.append('export const GRID: readonly (readonly Cell[])[] = [')
    for r in grid:
        cells_str = ', '.join(json.dumps(cell) for cell in r)
        lines.append(f'  [{cells_str}],')
    lines.append('];')
    lines.append('')

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text('\n'.join(lines))

    # Console summary.
    print(f'Wrote {OUTPUT}')
    print(f'Grid: {COLS}×{ROWS} = {COLS * ROWS} cells')
    for slug, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        pct = 100.0 * count / (COLS * ROWS)
        print(f'  {slug:<32} {count:>4} cells  ({pct:4.1f}%)')


if __name__ == '__main__':
    main()
