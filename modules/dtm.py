"""
DTM — Digital Terrain Model, Contour Lines, Volumes.

Pure-Python implementation with optional scipy/numpy acceleration.

Features:
  - Delaunay triangulation (TIN)
  - Contour line extraction (marching triangles)
  - Volume calculation (prism method between two surfaces)
  - Cross-section / profile extraction
  - Slope and aspect analysis
  - Breakline enforcement
  - Export to DXF (3D faces, contour polylines)

Works standalone without numpy/scipy but uses them when available
for better performance on large datasets.
"""

import math
from typing import Dict, Any, List, Optional, Tuple
from collections import defaultdict


# ================================================================
#  POINT / TRIANGLE TYPES
# ================================================================

class Point3D:
    """3D point with optional attributes."""
    __slots__ = ('x', 'y', 'z', 'name', 'code')

    def __init__(self, x: float, y: float, z: float,
                 name: str = "", code: str = ""):
        self.x = x
        self.y = y
        self.z = z
        self.name = name
        self.code = code

    def dist2d(self, other: 'Point3D') -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def dist3d(self, other: 'Point3D') -> float:
        return math.sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2
                         + (self.z - other.z) ** 2)


class Triangle:
    """A triangle in the TIN, referencing 3 point indices."""
    __slots__ = ('i0', 'i1', 'i2')

    def __init__(self, i0: int, i1: int, i2: int):
        self.i0 = i0
        self.i1 = i1
        self.i2 = i2

    def indices(self) -> Tuple[int, int, int]:
        return self.i0, self.i1, self.i2

    def edges(self) -> List[Tuple[int, int]]:
        return [(self.i0, self.i1), (self.i1, self.i2), (self.i2, self.i0)]


# ================================================================
#  TIN — TRIANGULATED IRREGULAR NETWORK
# ================================================================

class TIN:
    """
    Triangulated Irregular Network for terrain modeling.

    Uses scipy.spatial.Delaunay when available, falls back to
    a pure-Python incremental Bowyer-Watson implementation.
    """

    def __init__(self):
        self.points: List[Point3D] = []
        self.triangles: List[Triangle] = []
        self._built = False
        self._breakline_edges: List[Tuple[int, int]] = []

    def add_point(self, x: float, y: float, z: float,
                  name: str = "", code: str = ""):
        """Add a point to the TIN."""
        self.points.append(Point3D(x, y, z, name, code))
        self._built = False

    def add_points_from_features(self, features: List[Dict[str, Any]],
                                 use_local: bool = True,
                                 origin: Optional[Tuple[float, float]] = None):
        """
        Add points from GeoJSON features.
        If use_local=True and origin is given, converts to local ENU first.
        Otherwise uses lat/lon as x/y (suitable for small areas).
        """
        for feat in features:
            props = feat.get("properties", {})
            lat = props.get("lat")
            lon = props.get("lon")
            alt = props.get("alt_msl") or props.get("alt_hae", 0.0) or 0.0
            if lat is None or lon is None:
                continue

            if use_local and origin:
                # Simple local coordinates (adequate for small areas)
                dx = (lon - origin[1]) * 111320 * math.cos(math.radians(origin[0]))
                dy = (lat - origin[0]) * 110540
                self.add_point(dx, dy, alt, props.get("name", ""), props.get("codice", ""))
            else:
                self.add_point(lon, lat, alt, props.get("name", ""), props.get("codice", ""))

    def build(self):
        """Build Delaunay triangulation."""
        n = len(self.points)
        if n < 3:
            self.triangles = []
            self._built = True
            return

        try:
            self._build_scipy()
        except ImportError:
            self._build_python()
        self._built = True

    def _build_scipy(self):
        """Build using scipy.spatial.Delaunay."""
        from scipy.spatial import Delaunay
        import numpy as np

        coords = np.array([[p.x, p.y] for p in self.points])
        tri = Delaunay(coords)
        self.triangles = [Triangle(int(s[0]), int(s[1]), int(s[2]))
                          for s in tri.simplices]

    def _build_python(self):
        """Pure-Python Bowyer-Watson Delaunay triangulation."""
        pts = self.points
        n = len(pts)

        # Create super-triangle enclosing all points
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        dx = max(xs) - min(xs)
        dy = max(ys) - min(ys)
        dmax = max(dx, dy) * 2
        cx = (max(xs) + min(xs)) / 2
        cy = (max(ys) + min(ys)) / 2

        # Add 3 super-triangle vertices
        sp0 = Point3D(cx - 2 * dmax, cy - dmax, 0)
        sp1 = Point3D(cx + 2 * dmax, cy - dmax, 0)
        sp2 = Point3D(cx, cy + 2 * dmax, 0)
        all_pts = list(pts) + [sp0, sp1, sp2]
        si0, si1, si2 = n, n + 1, n + 2

        triangles = [(si0, si1, si2)]

        def circumcircle_contains(tri_idx, px, py):
            i0, i1, i2 = tri_idx
            ax, ay = all_pts[i0].x, all_pts[i0].y
            bx, by = all_pts[i1].x, all_pts[i1].y
            cx_, cy_ = all_pts[i2].x, all_pts[i2].y
            D = 2 * (ax * (by - cy_) + bx * (cy_ - ay) + cx_ * (ay - by))
            if abs(D) < 1e-12:
                return False
            ux = ((ax * ax + ay * ay) * (by - cy_) + (bx * bx + by * by) * (cy_ - ay) +
                  (cx_ * cx_ + cy_ * cy_) * (ay - by)) / D
            uy = ((ax * ax + ay * ay) * (cx_ - bx) + (bx * bx + by * by) * (ax - cx_) +
                  (cx_ * cx_ + cy_ * cy_) * (bx - ax)) / D
            r2 = (ax - ux) ** 2 + (ay - uy) ** 2
            return (px - ux) ** 2 + (py - uy) ** 2 <= r2

        # Insert points one by one
        for i in range(n):
            px, py = pts[i].x, pts[i].y
            bad = []
            for t in triangles:
                if circumcircle_contains(t, px, py):
                    bad.append(t)

            # Find boundary of the polygonal hole
            polygon = []
            for t in bad:
                edges = [(t[0], t[1]), (t[1], t[2]), (t[2], t[0])]
                for edge in edges:
                    shared = False
                    for other in bad:
                        if other == t:
                            continue
                        other_edges = [(other[0], other[1]), (other[1], other[2]),
                                       (other[2], other[0])]
                        if edge in other_edges or (edge[1], edge[0]) in other_edges:
                            shared = True
                            break
                    if not shared:
                        polygon.append(edge)

            # Remove bad triangles
            for t in bad:
                triangles.remove(t)

            # Create new triangles
            for edge in polygon:
                triangles.append((edge[0], edge[1], i))

        # Remove triangles with super-triangle vertices
        self.triangles = []
        super_indices = {si0, si1, si2}
        for t in triangles:
            if not (t[0] in super_indices or t[1] in super_indices or t[2] in super_indices):
                self.triangles.append(Triangle(t[0], t[1], t[2]))

    # ---------- Query ----------

    def interpolate_z(self, x: float, y: float) -> Optional[float]:
        """Interpolate Z value at (x, y) using the TIN."""
        if not self._built:
            self.build()

        for tri in self.triangles:
            p0 = self.points[tri.i0]
            p1 = self.points[tri.i1]
            p2 = self.points[tri.i2]

            # Barycentric coordinates
            denom = ((p1.y - p2.y) * (p0.x - p2.x) + (p2.x - p1.x) * (p0.y - p2.y))
            if abs(denom) < 1e-12:
                continue
            w0 = ((p1.y - p2.y) * (x - p2.x) + (p2.x - p1.x) * (y - p2.y)) / denom
            w1 = ((p2.y - p0.y) * (x - p2.x) + (p0.x - p2.x) * (y - p2.y)) / denom
            w2 = 1 - w0 - w1

            if w0 >= -1e-6 and w1 >= -1e-6 and w2 >= -1e-6:
                return w0 * p0.z + w1 * p1.z + w2 * p2.z

        return None

    def slope_aspect(self, tri_idx: int) -> Tuple[float, float]:
        """Calculate slope (degrees) and aspect (degrees from N) for a triangle."""
        tri = self.triangles[tri_idx]
        p0 = self.points[tri.i0]
        p1 = self.points[tri.i1]
        p2 = self.points[tri.i2]

        # Vectors
        v1x, v1y, v1z = p1.x - p0.x, p1.y - p0.y, p1.z - p0.z
        v2x, v2y, v2z = p2.x - p0.x, p2.y - p0.y, p2.z - p0.z

        # Normal vector (cross product)
        nx = v1y * v2z - v1z * v2y
        ny = v1z * v2x - v1x * v2z
        nz = v1x * v2y - v1y * v2x

        # Slope = angle from vertical
        n_len = math.sqrt(nx * nx + ny * ny + nz * nz)
        if n_len < 1e-12 or abs(nz) < 1e-12:
            return 90.0, 0.0

        slope_rad = math.acos(abs(nz) / n_len)
        slope_deg = math.degrees(slope_rad)

        # Aspect = direction of steepest descent
        aspect_rad = math.atan2(-nx, -ny)  # from North, clockwise
        aspect_deg = math.degrees(aspect_rad)
        if aspect_deg < 0:
            aspect_deg += 360

        return slope_deg, aspect_deg

    def triangle_area_3d(self, tri_idx: int) -> float:
        """3D surface area of a triangle."""
        tri = self.triangles[tri_idx]
        p0 = self.points[tri.i0]
        p1 = self.points[tri.i1]
        p2 = self.points[tri.i2]
        v1x, v1y, v1z = p1.x - p0.x, p1.y - p0.y, p1.z - p0.z
        v2x, v2y, v2z = p2.x - p0.x, p2.y - p0.y, p2.z - p0.z
        cx = v1y * v2z - v1z * v2y
        cy = v1z * v2x - v1x * v2z
        cz = v1x * v2y - v1y * v2x
        return 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)

    def add_breakline(self, points: List[Tuple[float, float, float]]):
        """
        Add a breakline to the TIN.

        Appends the breakline's 3D points to the TIN point list and records
        the consecutive edges so that enforce_breaklines() can force them
        into the triangulation after build().

        Args:
            points: list of (x, y, z) tuples defining the breakline vertices.
        """
        if len(points) < 2:
            return
        start_idx = len(self.points)
        for x, y, z in points:
            self.add_point(x, y, z)
        for i in range(len(points) - 1):
            self._breakline_edges.append((start_idx + i, start_idx + i + 1))
        self._built = False

    def enforce_breaklines(self):
        """
        Force breakline edges into the triangulation after build().

        For each required edge (a, b) that is not already present in the
        triangulation, find the two triangles sharing the opposite diagonal
        and flip it to restore the edge.  The process iterates until all
        breakline edges are present or no further flip is possible.
        """
        if not self._built:
            self.build()

        if not self._breakline_edges:
            return

        def _edges_set():
            """Return the set of canonical directed edges in the triangulation."""
            es = set()
            for t in self.triangles:
                for a, b in t.edges():
                    es.add((min(a, b), max(a, b)))
            return es

        def _find_flip(ea, eb):
            """
            Find the pair of triangles sharing edge (ec, ed) that is the
            diagonal of the quad containing (ea, eb) and flip it.
            Returns True if a flip was performed.
            """
            # Locate triangles that contain both ea and eb among their vertices
            # but do NOT already have the edge ea-eb.
            # We look for triangles that share the "opposite" edge.
            tris_with_a = [t for t in self.triangles if ea in t.indices()]
            tris_with_b = [t for t in self.triangles if eb in t.indices()]

            # Find two triangles forming a quadrilateral that contains ea and eb
            # as opposite corners (i.e., ea-eb is the diagonal we want to insert).
            for ta in tris_with_a:
                for tb in tris_with_b:
                    if ta is tb:
                        continue
                    set_a = set(ta.indices())
                    set_b = set(tb.indices())
                    shared = set_a & set_b
                    if len(shared) == 2:
                        # They share an edge; check if flipping gives ea-eb
                        ec, ed = tuple(shared)
                        other_a = (set_a - shared).pop()
                        other_b = (set_b - shared).pop()
                        if {other_a, other_b} == {ea, eb}:
                            # Flip: remove ta and tb, add two new triangles
                            self.triangles.remove(ta)
                            self.triangles.remove(tb)
                            self.triangles.append(Triangle(ea, eb, ec))
                            self.triangles.append(Triangle(ea, eb, ed))
                            return True
            return False

        max_passes = len(self._breakline_edges) * 3
        for _ in range(max_passes):
            current_edges = _edges_set()
            all_present = True
            for ea, eb in self._breakline_edges:
                key = (min(ea, eb), max(ea, eb))
                if key not in current_edges:
                    all_present = False
                    _find_flip(ea, eb)
            if all_present:
                break


# ================================================================
#  CONTOUR LINES (Marching Triangles)
# ================================================================

def extract_contours(tin: TIN, interval: float = 1.0,
                     min_z: Optional[float] = None,
                     max_z: Optional[float] = None
                     ) -> Dict[float, List[List[Tuple[float, float]]]]:
    """
    Extract contour lines from a TIN using marching triangles.

    Returns: {elevation: [segment_list]} where each segment is [(x, y), ...]
    """
    if not tin._built:
        tin.build()

    pts = tin.points
    z_min = min_z if min_z is not None else min(p.z for p in pts)
    z_max = max_z if max_z is not None else max(p.z for p in pts)

    # Round to interval
    z_start = math.floor(z_min / interval) * interval
    z_end = math.ceil(z_max / interval) * interval

    contours: Dict[float, List[List[Tuple[float, float]]]] = {}

    z_level = z_start
    while z_level <= z_end:
        segments = []
        for tri in tin.triangles:
            seg = _contour_triangle(pts, tri, z_level)
            if seg:
                segments.append(seg)

        if segments:
            # Chain segments into polylines
            polylines = _chain_segments(segments)
            contours[z_level] = polylines

        z_level += interval

    return contours


def _contour_triangle(pts: List[Point3D], tri: Triangle,
                      z: float) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Find contour segment crossing a triangle at elevation z."""
    p0, p1, p2 = pts[tri.i0], pts[tri.i1], pts[tri.i2]
    z0, z1, z2 = p0.z, p1.z, p2.z

    crossings = []

    for pa, pb, za, zb in [(p0, p1, z0, z1), (p1, p2, z1, z2), (p2, p0, z2, z0)]:
        if (za - z) * (zb - z) < 0:  # crosses
            t = (z - za) / (zb - za)
            cx = pa.x + t * (pb.x - pa.x)
            cy = pa.y + t * (pb.y - pa.y)
            crossings.append((cx, cy))
        elif abs(za - z) < 1e-9:
            crossings.append((pa.x, pa.y))

    # Remove duplicates
    unique = []
    for c in crossings:
        is_dup = False
        for u in unique:
            if abs(c[0] - u[0]) < 1e-9 and abs(c[1] - u[1]) < 1e-9:
                is_dup = True
                break
        if not is_dup:
            unique.append(c)

    if len(unique) == 2:
        return (unique[0], unique[1])
    return None


def _chain_segments(segments: List[Tuple[Tuple[float, float], Tuple[float, float]]]
                    ) -> List[List[Tuple[float, float]]]:
    """Chain disconnected segments into continuous polylines."""
    if not segments:
        return []

    EPS = 1e-6
    used = [False] * len(segments)
    polylines = []

    for start_idx in range(len(segments)):
        if used[start_idx]:
            continue
        used[start_idx] = True
        chain = [segments[start_idx][0], segments[start_idx][1]]

        changed = True
        while changed:
            changed = False
            for i in range(len(segments)):
                if used[i]:
                    continue
                s = segments[i]
                if _pts_close(chain[-1], s[0], EPS):
                    chain.append(s[1])
                    used[i] = True
                    changed = True
                elif _pts_close(chain[-1], s[1], EPS):
                    chain.append(s[0])
                    used[i] = True
                    changed = True
                elif _pts_close(chain[0], s[1], EPS):
                    chain.insert(0, s[0])
                    used[i] = True
                    changed = True
                elif _pts_close(chain[0], s[0], EPS):
                    chain.insert(0, s[1])
                    used[i] = True
                    changed = True

        polylines.append(chain)

    return polylines


def _pts_close(a: Tuple[float, float], b: Tuple[float, float], eps: float) -> bool:
    return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps


# ================================================================
#  VOLUME CALCULATION
# ================================================================

def volume_between_surfaces(tin_terrain: TIN, reference_z: float
                            ) -> Dict[str, float]:
    """
    Calculate cut/fill volumes between TIN and a flat reference plane.

    Returns: {"cut": m³, "fill": m³, "net": m³, "area_2d": m², "area_3d": m²}
    """
    if not tin_terrain._built:
        tin_terrain.build()

    cut_vol = 0.0
    fill_vol = 0.0
    area_2d = 0.0
    area_3d = 0.0

    for i, tri in enumerate(tin_terrain.triangles):
        p0 = tin_terrain.points[tri.i0]
        p1 = tin_terrain.points[tri.i1]
        p2 = tin_terrain.points[tri.i2]

        # 2D area (Shoelace)
        a2d = abs((p1.x - p0.x) * (p2.y - p0.y) - (p2.x - p0.x) * (p1.y - p0.y)) / 2

        # 3D area
        a3d = tin_terrain.triangle_area_3d(i)

        # Average height above/below reference
        z_avg = (p0.z + p1.z + p2.z) / 3.0
        dz = z_avg - reference_z

        # Prism volume
        vol = a2d * abs(dz)
        if dz > 0:
            cut_vol += vol
        else:
            fill_vol += vol

        area_2d += a2d
        area_3d += a3d

    return {
        "cut": cut_vol,
        "fill": fill_vol,
        "net": cut_vol - fill_vol,
        "area_2d": area_2d,
        "area_3d": area_3d,
        "reference_z": reference_z,
    }


def volume_between_tins(tin_upper: TIN, tin_lower: TIN,
                        grid_step: float = 1.0) -> Dict[str, float]:
    """
    Volume between two TIN surfaces by grid sampling.
    Samples both surfaces on a regular grid and integrates.
    """
    if not tin_upper._built:
        tin_upper.build()
    if not tin_lower._built:
        tin_lower.build()

    all_pts = tin_upper.points + tin_lower.points
    x_min = min(p.x for p in all_pts)
    x_max = max(p.x for p in all_pts)
    y_min = min(p.y for p in all_pts)
    y_max = max(p.y for p in all_pts)

    vol = 0.0
    n_samples = 0
    cell_area = grid_step * grid_step

    y = y_min
    while y <= y_max:
        x = x_min
        while x <= x_max:
            z_up = tin_upper.interpolate_z(x, y)
            z_lo = tin_lower.interpolate_z(x, y)
            if z_up is not None and z_lo is not None:
                vol += (z_up - z_lo) * cell_area
                n_samples += 1
            x += grid_step
        y += grid_step

    return {
        "volume": vol,
        "grid_step": grid_step,
        "n_samples": n_samples,
        "area_approx": n_samples * cell_area,
    }


# ================================================================
#  PROFILE / CROSS-SECTION EXTRACTION
# ================================================================

def extract_profile(tin: TIN,
                    start: Tuple[float, float],
                    end: Tuple[float, float],
                    step: float = 1.0) -> List[Dict[str, float]]:
    """
    Extract a terrain profile along a line.

    Returns list of {"station": float, "x": float, "y": float, "z": float}
    """
    if not tin._built:
        tin.build()

    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return []

    ux = dx / length
    uy = dy / length

    profile = []
    station = 0.0
    while station <= length:
        x = start[0] + station * ux
        y = start[1] + station * uy
        z = tin.interpolate_z(x, y)
        if z is not None:
            profile.append({"station": station, "x": x, "y": y, "z": z})
        station += step

    return profile


def extract_cross_sections(tin: TIN,
                           alignment: List[Tuple[float, float]],
                           interval: float = 10.0,
                           width: float = 20.0,
                           step: float = 1.0
                           ) -> List[Dict[str, Any]]:
    """
    Extract cross-sections perpendicular to an alignment.

    alignment: list of (x, y) points defining the centerline
    interval: distance between sections along alignment
    width: total section width (half on each side)
    step: sample spacing within section

    Returns list of sections, each with profile data.
    """
    if not tin._built:
        tin.build()

    if len(alignment) < 2:
        return []

    # Compute cumulative stations along alignment
    stations = [0.0]
    for i in range(1, len(alignment)):
        dx = alignment[i][0] - alignment[i - 1][0]
        dy = alignment[i][1] - alignment[i - 1][1]
        stations.append(stations[-1] + math.hypot(dx, dy))
    total_length = stations[-1]

    sections = []
    dist = 0.0
    while dist <= total_length:
        # Find position and direction on alignment at this station
        pos, direction = _interpolate_alignment(alignment, stations, dist)
        if pos is None:
            dist += interval
            continue

        # Perpendicular direction
        perp = (-direction[1], direction[0])

        # Extract section
        hw = width / 2
        profile = []
        s = -hw
        while s <= hw:
            x = pos[0] + s * perp[0]
            y = pos[1] + s * perp[1]
            z = tin.interpolate_z(x, y)
            if z is not None:
                profile.append({"offset": s, "x": x, "y": y, "z": z})
            s += step

        sections.append({
            "station": dist,
            "center": pos,
            "profile": profile,
        })
        dist += interval

    return sections


def _interpolate_alignment(alignment, stations, dist):
    """Find position and unit direction vector at a given distance along alignment."""
    for i in range(len(stations) - 1):
        if stations[i] <= dist <= stations[i + 1]:
            seg_len = stations[i + 1] - stations[i]
            if seg_len < 1e-9:
                continue
            t = (dist - stations[i]) / seg_len
            x = alignment[i][0] + t * (alignment[i + 1][0] - alignment[i][0])
            y = alignment[i][1] + t * (alignment[i + 1][1] - alignment[i][1])
            dx = alignment[i + 1][0] - alignment[i][0]
            dy = alignment[i + 1][1] - alignment[i][1]
            d_len = math.hypot(dx, dy)
            return (x, y), (dx / d_len, dy / d_len)
    return None, None


# ================================================================
#  DTM STATISTICS
# ================================================================

def tin_statistics(tin: TIN) -> Dict[str, Any]:
    """Compute statistics for a TIN surface."""
    if not tin._built:
        tin.build()

    if not tin.points:
        return {"error": "Nessun punto"}

    zs = [p.z for p in tin.points]
    z_min = min(zs)
    z_max = max(zs)
    z_mean = sum(zs) / len(zs)

    slopes = []
    areas_2d = []
    areas_3d = []
    for i, tri in enumerate(tin.triangles):
        s, a = tin.slope_aspect(i)
        slopes.append(s)
        p0 = tin.points[tri.i0]
        p1 = tin.points[tri.i1]
        p2 = tin.points[tri.i2]
        a2d = abs((p1.x - p0.x) * (p2.y - p0.y) - (p2.x - p0.x) * (p1.y - p0.y)) / 2
        areas_2d.append(a2d)
        areas_3d.append(tin.triangle_area_3d(i))

    return {
        "n_points": len(tin.points),
        "n_triangles": len(tin.triangles),
        "z_min": z_min,
        "z_max": z_max,
        "z_mean": z_mean,
        "z_range": z_max - z_min,
        "slope_min": min(slopes) if slopes else 0,
        "slope_max": max(slopes) if slopes else 0,
        "slope_mean": sum(slopes) / len(slopes) if slopes else 0,
        "area_2d": sum(areas_2d),
        "area_3d": sum(areas_3d),
    }
