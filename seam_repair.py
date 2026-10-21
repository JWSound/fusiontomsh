"""Conservative CAD repair for separately exported planar surface caps.

Coordinates are in STEP/Gmsh millimetres. Source Fusion bodies are never edited.
"""

from collections import Counter
import math


SEAM_TOLERANCE_MM = 0.01
CURVE_SAMPLES = 257
PLANE_TOLERANCE_MM = 1e-6


def _distance(a, b):
    return math.dist(a, b)


def _normal(gmsh, surface):
    lower, upper = gmsh.model.getParametrizationBounds(2, surface)
    return tuple(gmsh.model.getNormal(surface, [(a + b) / 2 for a, b in zip(lower, upper)]))


def _curve(gmsh, tag):
    lower, upper = gmsh.model.getParametrizationBounds(1, tag)
    parameters = [lower[0] + (upper[0] - lower[0]) * i / (CURVE_SAMPLES - 1)
                  for i in range(CURVE_SAMPLES)]
    coordinates = list(gmsh.model.getValue(1, tag, parameters))
    vertices = [t for d, t in gmsh.model.getBoundary([(1, tag)], oriented=False) if d == 0]
    if len(vertices) != 2:
        return None  # Periodic edges need a different loop reconstruction policy.
    return {
        'tag': tag,
        'vertices': vertices,
        'ends': [tuple(gmsh.model.getValue(0, t, [])) for t in vertices],
        'coordinates': coordinates,
        'bounds': (float(lower[0]), float(upper[0])),
        'length': gmsh.model.occ.getMass(1, tag),
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
    for a, b in ((source, target), (target, source)):
        closest, parameters = gmsh.model.getClosestPoint(1, b['tag'], a['coordinates'])
        if (len(closest) != len(a['coordinates'])
                or len(parameters) * 3 != len(closest)
                or not all(math.isfinite(v) for v in (*closest, *parameters))):
            return None
        lo, hi = b['bounds']
        epsilon = max(1e-9, abs(hi - lo) * 1e-7)
        if any(t < lo - epsilon or t > hi + epsilon for t in parameters):
            return None  # Gmsh can project outside a trimmed curve.
        if any(_distance(a['coordinates'][i:i + 3], closest[i:i + 3]) > SEAM_TOLERANCE_MM
               for i in range(0, len(closest), 3)):
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
