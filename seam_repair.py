"""Conservative CAD repair for separately exported planar surface caps.

Coordinates are in STEP/Gmsh millimetres. Source Fusion bodies are never edited.
"""

from collections import Counter
import math


SEAM_TOLERANCE_MM = 0.01
CURVE_SAMPLES = 257
DENSE_CURVE_SAMPLES = 2049
POLYLINE_ERROR_MM = SEAM_TOLERANCE_MM / 100
MAX_REFINEMENTS = 8
PLANE_TOLERANCE_MM = 1e-6


def _distance(a, b):
    return math.dist(a, b)


def _normal(gmsh, surface):
    lower, upper = gmsh.model.getParametrizationBounds(2, surface)
    return tuple(gmsh.model.getNormal(surface, [(a + b) / 2 for a, b in zip(lower, upper)]))


def _sample(gmsh, tag, lower, upper, count):
    parameters = [lower + (upper - lower) * i / (count - 1) for i in range(count)]
    return _values(gmsh, tag, parameters)


def _values(gmsh, tag, parameters):
    values = [float(v) for v in gmsh.model.getValue(1, tag, parameters)]
    if len(values) != 3 * len(parameters) or not all(math.isfinite(v) for v in values):
        raise ValueError('Invalid curve samples')
    return values


def _polyline(gmsh, tag, lower, upper):
    """Refine chords using quarter-point checks; reject unresolved curvature.

    This is a sampled accuracy check, not a mathematical bound for arbitrary
    splines. Keep a small error budget inside the seam tolerance.
    """
    parameters = [lower + (upper - lower) * i / (DENSE_CURVE_SAMPLES - 1)
                  for i in range(DENSE_CURVE_SAMPLES)]
    values = _values(gmsh, tag, parameters)
    points = [values[i:i + 3] for i in range(0, len(values), 3)]
    pending = list(zip(parameters, parameters[1:], points, points[1:]))
    accepted = []
    for _ in range(MAX_REFINEMENTS):
        checks = [a + (b - a) * f for a, b, _, _ in pending for f in (.25, .5, .75)]
        samples = _values(gmsh, tag, checks)
        refined = []
        for i, (a, b, p, q) in enumerate(pending):
            interior = [samples[9*i + 3*j:9*i + 3*j + 3] for j in range(3)]
            error = max(math.dist(x, [p[k] + f * (q[k] - p[k]) for k in range(3)])
                        for x, f in zip(interior, (.25, .5, .75)))
            if error <= POLYLINE_ERROR_MM:
                accepted.append((a, p, q))
            else:
                ts = [a, *checks[3*i:3*i + 3], b]
                ps = [p, *interior, q]
                refined.extend(zip(ts, ts[1:], ps, ps[1:]))
        if not refined:
            accepted.sort(key=lambda item: item[0])
            return [v for _, p, _ in accepted for v in p] + list(accepted[-1][2])
        if len(refined) + len(accepted) > 32768:
            break
        pending = refined
    raise ValueError('Curve sampling did not converge')


def _max_distance_to_polyline(coordinates, polyline):
    """Conservative sampled distance for a seam-tolerance decision.

    The local search may overestimate the nearest distance when it already
    meets the acceptance threshold; otherwise search every segment.

    The polyline covers only the trimmed curve, so points beyond its ends are
    measured to the end segment instead of to an untrimmed extension.
    """
    if (not coordinates or len(coordinates) % 3 or len(polyline) < 6
            or len(polyline) % 3
            or not all(math.isfinite(v) for v in (*coordinates, *polyline))):
        return math.inf
    segments = []
    for i in range(0, len(polyline) - 3, 3):
        p, q = polyline[i:i + 3], polyline[i + 3:i + 6]
        d = (q[0] - p[0], q[1] - p[1], q[2] - p[2])
        segments.append((p, d, d[0] * d[0] + d[1] * d[1] + d[2] * d[2]))

    worst = 0.0
    hint = 0
    for i in range(0, len(coordinates), 3):
        x = coordinates[i:i + 3]

        def distance(index):
            p, d, length2 = segments[index]
            w = (x[0] - p[0], x[1] - p[1], x[2] - p[2])
            t = 0.0 if length2 <= 0 else max(0.0, min(1.0, (w[0] * d[0] + w[1] * d[1] + w[2] * d[2]) / length2))
            return math.dist(x, (p[0] + t * d[0], p[1] + t * d[1], p[2] + t * d[2]))

        # Samples advance monotonically along both curves: search near the
        # previous hit first and fall back to a full scan only if needed.
        window = range(max(0, hint - 40), min(len(segments), hint + 40))
        best_index = min(window, key=distance)
        best = distance(best_index)
        if best > SEAM_TOLERANCE_MM - POLYLINE_ERROR_MM:
            best_index = min(range(len(segments)), key=distance)
            best = distance(best_index)
        hint = best_index
        worst = max(worst, best)
    return worst


def _curve(gmsh, tag):
    try:
        return _validated_curve(gmsh, tag)
    except ValueError:
        return None


def _validated_curve(gmsh, tag):
    lower, upper = gmsh.model.getParametrizationBounds(1, tag)
    if not (math.isfinite(lower[0]) and math.isfinite(upper[0]) and lower[0] < upper[0]):
        return None
    coordinates = _sample(gmsh, tag, lower[0], upper[0], CURVE_SAMPLES)
    vertices = [t for d, t in gmsh.model.getBoundary([(1, tag)], oriented=False) if d == 0]
    if len(vertices) != 2:
        return None  # Periodic edges need a different loop reconstruction policy.
    ends = [tuple(gmsh.model.getValue(0, t, [])) for t in vertices]
    length = gmsh.model.occ.getMass(1, tag)
    if (any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in ends)
            or not math.isfinite(length) or length <= 0):
        return None
    return {
        'tag': tag,
        'vertices': vertices,
        'ends': ends,
        'coordinates': coordinates,
        'dense': _polyline(gmsh, tag, lower[0], upper[0]),
        'length': length,
    }


def _matches(gmsh, source, target, normal, plane_point):
    if abs(source['length'] - target['length']) > 2 * SEAM_TOLERANCE_MM:
        return None
    order = None
    for candidate in ((0, 1), (1, 0)):
        if all(_distance(source['ends'][i], target['ends'][j]) <= SEAM_TOLERANCE_MM
               for i, j in enumerate(candidate)):
            order = candidate
            break
    if order is None:
        return None
    xyz = target['coordinates']
    if any(abs(sum(normal[j] * (xyz[i + j] - plane_point[j]) for j in range(3)))
           > PLANE_TOLERANCE_MM for i in range(0, len(xyz), 3)):
        return None
    # Test both directions: an endpoint match alone can bridge unrelated arcs.
    # Measure against a dense polyline of the other curve rather than using
    # gmsh.model.getClosestPoint: macOS arm64 users reported OCC projection
    # silently failing after the first export in a Fusion session (every
    # returned parameter is 1.0), which skipped the repair and left slivers.
    for a, b in ((source, target), (target, source)):
        if _max_distance_to_polyline(a['coordinates'], b['dense']) > SEAM_TOLERANCE_MM - POLYLINE_ERROR_MM:
            return None
    return dict(zip(source['vertices'], [target['vertices'][i] for i in order]))


def _simple_loop(curves):
    """Accept one connected loop, without holes or branch vertices."""
    adjacency = {}
    for curve in curves:
        a, b = curve['vertices']
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)
    if not adjacency or any(len(neighbors) != 2 for neighbors in adjacency.values()):
        return False
    visited, pending = set(), [next(iter(adjacency))]
    while pending:
        vertex = pending.pop()
        if vertex not in visited:
            visited.add(vertex)
            pending.extend(adjacency[vertex])
    return len(visited) == len(adjacency)


def repair_planar_caps(gmsh, body_dim_tags):
    """Reuse unique near-coincident free rim edges on single-face planar bodies.

    Leave solids, multi-face caps, holes, periodic edges, ambiguous matches and
    larger gaps alone. Return updated body entities and original cap normals for
    orientation restoration after Boolean fragmentation.
    """
    bodies = {i: list(entities) for i, entities in enumerate(body_dim_tags)}
    normals = {}
    for body_index in list(bodies):
        entities = bodies[body_index]
        if len(entities) != 1 or entities[0][0] != 2:
            continue
        surface = entities[0][1]
        if gmsh.model.getType(2, surface) != 'Plane':
            continue
        boundary = gmsh.model.getBoundary([(2, surface)], oriented=False)
        curves = [_curve(gmsh, t) for d, t in boundary if d == 1]
        if not curves or any(c is None for c in curves) or not _simple_loop(curves):
            continue
        normal = _normal(gmsh, surface)
        plane_point = curves[0]['ends'][0]
        donor_tags = set()
        for other_index, other_entities in bodies.items():
            if other_index == body_index or any(d != 2 for d, _ in other_entities):
                continue
            counts = Counter(t for d, t in gmsh.model.getBoundary(
                other_entities, combined=False, oriented=False) if d == 1)
            donor_tags.update(t for t, count in counts.items() if count == 1)
        donors = [c for t in sorted(donor_tags) if (c := _curve(gmsh, t)) is not None]
        replacements, vertex_map = {}, {}
        unsafe = False
        for curve in curves:
            matches = []
            for donor in donors:
                mapping = _matches(gmsh, curve, donor, normal, plane_point)
                if mapping is not None:
                    matches.append((donor['tag'], mapping))
            if len(matches) > 1:
                unsafe = True
                break
            if matches:
                replacement, mapping = matches[0]
                replacements[curve['tag']] = replacement
                for old, new in mapping.items():
                    if old in vertex_map and vertex_map[old] != new:
                        unsafe = True
                    vertex_map[old] = new
        if unsafe or not any(old != new for old, new in replacements.items()):
            continue
        # Only straight remaining edges can safely have endpoints reattached.
        if any(c['tag'] not in replacements
               and any(v in vertex_map and vertex_map[v] != v for v in c['vertices'])
               and gmsh.model.getType(1, c['tag']) != 'Line' for c in curves):
            continue
        created_curves = []
        new_surface = None
        try:
            new_boundary = []
            for curve in curves:
                tag = curve['tag']
                if tag in replacements:
                    new_boundary.append(replacements[tag])
                elif any(vertex_map.get(v, v) != v for v in curve['vertices']):
                    a, b = [vertex_map.get(v, v) for v in curve['vertices']]
                    line = gmsh.model.occ.addLine(a, b)
                    created_curves.append(line)
                    new_boundary.append(line)
                else:
                    new_boundary.append(tag)
            wire = gmsh.model.occ.addWire(new_boundary, checkClosed=True)
            new_surface = gmsh.model.occ.addPlaneSurface([wire])
            gmsh.model.occ.synchronize()
            # Bound the area change as well as the sampled boundary displacement.
            area_change = abs(gmsh.model.occ.getMass(2, new_surface)
                              - gmsh.model.occ.getMass(2, surface))
            if area_change > sum(c['length'] for c in curves) * SEAM_TOLERANCE_MM:
                raise ValueError('Planar seam repair changed too much surface area')
        except Exception:
            if new_surface is not None:
                gmsh.model.occ.remove([(2, new_surface)], recursive=False)
            if created_curves:
                gmsh.model.occ.remove([(1, t) for t in created_curves], recursive=False)
            gmsh.model.occ.synchronize()
            continue
        gmsh.model.occ.remove([(2, surface)], recursive=True)
        gmsh.model.occ.synchronize()
        bodies[body_index] = [(2, new_surface)]
        normals[body_index] = normal
    return [bodies[i] for i in range(len(bodies))], normals


def restore_cap_orientation(gmsh, surfaces, original_normal):
    for surface in surfaces:
        normal = _normal(gmsh, surface)
        if sum(a * b for a, b in zip(normal, original_normal)) < 0:
            gmsh.model.mesh.setReverse(2, surface, True)
