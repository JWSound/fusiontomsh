"""Conservative mesh-only recovery of a skipped CAD corner triangle.

Not a general hole filler: two sides must be existing 1D CAD mesh segments,
and the third must be a diagonal on their uniquely shared, closed-solid face.
All proposals are checked before any elements are added. Units are millimetres.
"""

from collections import Counter, defaultdict
import math


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _fail(reason):
    raise RuntimeError("FEM boundary repair refused: " + reason + ". Output was not written.")


def _triangles(gmsh):
    result = []
    for _, face in gmsh.model.getEntities(2):
        types, _, connectivity = gmsh.model.mesh.getElements(2, face)
        for kind, nodes in zip(types, connectivity):
            if int(kind) != 2:
                _fail("only first-order triangular boundaries are supported")
            result.extend((int(face), tuple(map(int, nodes[i:i+3])))
                          for i in range(0, len(nodes), 3))
    return result


def _edges(triangles):
    edges = defaultdict(list)
    for face, (a, b, c) in triangles:
        for u, v in ((a, b), (b, c), (c, a)):
            edges[tuple(sorted((u, v)))].append((face, u, v))
    return edges


def _loops(edges):
    """Return reversed boundary cycles, rejecting branches and larger holes."""
    outgoing = {}
    for entries in edges.values():
        if len(entries) > 2:
            _fail("non-manifold surface edges")
        if len(entries) == 2:
            if entries[0][1:] != entries[1][1:][::-1]:
                _fail("inconsistent surface orientation")
        else:
            _, v, u = entries[0]
            if u in outgoing:
                _fail("branching boundary")
            outgoing[u] = v
    if set(outgoing) != set(outgoing.values()):
        _fail("open or branching boundary chain")
    cycles = []
    while outgoing:
        start = next(iter(outgoing))
        cycle = [start]
        node = outgoing.pop(start)
        while node != start:
            if node not in outgoing or len(cycle) >= 3:
                _fail("the opening is not an isolated triangle")
            cycle.append(node)
            node = outgoing.pop(node)
        if len(cycle) != 3:
            _fail("degenerate opening")
        cycles.append(tuple(cycle))
    return cycles


def physical_groups(gmsh):
    return sorted((int(d), int(t), gmsh.model.getPhysicalName(d, t),
                   tuple(sorted(map(int, gmsh.model.getEntitiesForPhysicalGroup(d, t)))))
                  for d, t in gmsh.model.getPhysicalGroups())


def _on_shared(point, shared, eps):
    if not shared:
        return False
    if len(shared) == 1:
        return _norm(_sub(point, shared[0])) <= eps
    ab = _sub(shared[1], shared[0])
    t = _dot(_sub(point, shared[0]), ab) / _dot(ab, ab)
    nearest = tuple(a + max(0., min(1., t))*b for a, b in zip(shared[0], ab))
    return _norm(_sub(point, nearest)) <= eps


def _inside_triangle(p, triangle, eps):
    a, b, c = triangle
    u, v, w = _sub(b, a), _sub(c, a), _sub(p, a)
    uu, uv, vv = _dot(u, u), _dot(u, v), _dot(v, v)
    determinant = uu*vv - uv*uv
    if determinant <= 0:
        return False
    s = (vv*_dot(w, u)-uv*_dot(w, v))/determinant
    t = (uu*_dot(w, v)-uv*_dot(w, u))/determinant
    tol = eps / max(_norm(u), _norm(v), eps)
    return s >= -tol and t >= -tol and s+t <= 1+tol


def _intersects(a, b, shared, eps=1e-8):
    """Triangle intersection beyond explicitly shared vertices/edges."""
    if any(max(p[k] for p in a) < min(p[k] for p in b)-eps or
           max(p[k] for p in b) < min(p[k] for p in a)-eps for k in range(3)):
        return False
    na, nb = (_cross(_sub(t[1], t[0]), _sub(t[2], t[0])) for t in (a, b))
    if min(_norm(na), _norm(nb)) <= eps*eps:
        return True  # Cannot safely rule out an overlap with a degenerate facet.
    na = tuple(v/_norm(na) for v in na)
    nb = tuple(v/_norm(nb) for v in nb)
    if max(abs(_dot(_sub(p, b[0]), nb)) for p in a) <= eps:
        # Clip in the dominant plane; positive overlap or unshared touching fails.
        drop = max(range(3), key=lambda k: abs(nb[k]))
        axes = [k for k in range(3) if k != drop]
        def orient(u, v, p):
            return ((v[axes[0]]-u[axes[0]])*(p[axes[1]]-u[axes[1]]) -
                    (v[axes[1]]-u[axes[1]])*(p[axes[0]]-u[axes[0]]))
        sign = 1 if orient(*b) > 0 else -1
        polygon = list(a)
        for u, v in zip(b, b[1:]+b[:1]):
            clipped = []
            if not polygon:
                break
            previous = polygon[-1]
            dp = sign*orient(u, v, previous)
            for point in polygon:
                dc = sign*orient(u, v, point)
                if (dc >= 0) != (dp >= 0):
                    t = dp/(dp-dc)
                    clipped.append(tuple(x+t*(y-x) for x, y in zip(previous, point)))
                if dc >= 0:
                    clipped.append(point)
                previous, dp = point, dc
            polygon = clipped
        return any(not _on_shared(p, shared, eps) for p in polygon)
    for source, target, normal in ((a, b, nb), (b, a, na)):
        for u, v in zip(source, source[1:]+source[:1]):
            du, dv = (_dot(_sub(p, target[0]), normal) for p in (u, v))
            hits = []
            if abs(du) <= eps:
                hits.append(u)
            if du*dv < 0:
                t = du/(du-dv)
                hits.append(tuple(x+t*(y-x) for x, y in zip(u, v)))
            for point in hits:
                if _inside_triangle(point, target, eps) and not _on_shared(point, shared, eps):
                    return True
    return False


def repair_boundary(gmsh, max_size_mm):
    triangles = _triangles(gmsh)
    edges = _edges(triangles)
    cycles = _loops(edges)
    if not cycles:
        return []
    volumes = gmsh.model.getEntities(3)
    if len(volumes) != 1 or len(cycles) > 8:
        _fail("repair requires one solid and at most eight isolated patches")
    faces = {abs(int(t)) for d, t in gmsh.model.getBoundary(volumes, oriented=True)}
    if {f for f, _ in triangles} != faces:
        _fail("mesh faces do not match the solid boundary")

    cad_edges = defaultdict(set)
    incidence = defaultdict(list)
    for face in faces:
        for _, curve in gmsh.model.getBoundary([(2, face)], oriented=False):
            incidence[int(curve)].append(face)
    for curve, owners in incidence.items():
        types, _, connectivity = gmsh.model.mesh.getElements(1, curve)
        for kind, nodes in zip(types, connectivity):
            if int(kind) != 1:
                _fail("unsupported CAD edge mesh")
            if len(owners) != 2:
                _fail("the imported CAD boundary has an unshared edge")
            for i in range(0, len(nodes), 2):
                cad_edges[tuple(sorted(map(int, nodes[i:i+2])))].add(curve)
    tags, coordinates, _ = gmsh.model.mesh.getNodes()
    xyz = {int(n): tuple(map(float, coordinates[3*i:3*i+3])) for i, n in enumerate(tags)}
    proposals = []
    for cycle in cycles:
        sides = [tuple(sorted((cycle[i], cycle[(i+1)%3]))) for i in range(3)]
        diagonal = [side for side in sides if side not in cad_edges]
        if len(diagonal) != 1:
            _fail("patch must have two CAD edges and one face-mesh diagonal")
        face = edges[diagonal[0]][0][0]
        candidates = None
        for side in sides:
            if side == diagonal[0]:
                continue
            curves = cad_edges[side]
            if len(curves) != 1:
                _fail("ambiguous CAD edge ownership")
            owners = set(incidence[next(iter(curves))])
            candidates = owners if candidates is None else candidates & owners
            if edges[side][0][0] == face or edges[side][0][0] not in owners:
                _fail("CAD and mesh edge ownership disagree")
        if candidates != {face}:
            _fail("no unique owning CAD face")
        points = tuple(xyz[n] for n in cycle)
        lengths = [_norm(_sub(points[i], points[(i+1)%3])) for i in range(3)]
        normal = _cross(_sub(points[1], points[0]), _sub(points[2], points[0]))
        area = _norm(normal)/2
        if (not all(math.isfinite(x) for p in points for x in p) or
                min(lengths) <= 1e-7 or max(lengths) > 2*max_size_mm or
                area > max_size_mm**2 or area/max(lengths)**2 < 1e-4):
            _fail("patch size or shape is outside repair limits")
        tolerance = min(0.001, min(lengths)*0.01)
        # Boundary samples can be outside trimming tolerance after linearization;
        # every strictly interior sample must be contained in the owning face.
        weights = [(i/10, j/10, (10-i-j)/10)
                   for i in range(11) for j in range(11-i)]
        samples = [tuple(sum(w*p[k] for w, p in zip(ws, points)) for k in range(3))
                   for ws in weights]
        projected, uv = gmsh.model.getClosestPoint(2, face, [v for p in samples for v in p])
        if len(projected) != 3*len(samples) or len(uv) != 2*len(samples):
            _fail("CAD projection failed")
        distances = [_norm(_sub(p, projected[3*i:3*i+3])) for i, p in enumerate(samples)]
        if not all(math.isfinite(d) and d <= tolerance for d in distances):
            _fail("patch departs from the CAD surface")
        for i, ws in enumerate(weights):
            if min(ws) > 0 and not gmsh.model.isInside(2, face, uv[2*i:2*i+2], True):
                _fail("patch crosses a trimmed CAD boundary")
        normals = gmsh.model.getNormal(face, uv)
        if len(normals) != 3*len(samples):
            _fail("CAD normals unavailable")
        for i in range(len(samples)):
            n = normals[3*i:3*i+3]
            denominator = _norm(n)*_norm(normal)
            alignment = abs(_dot(n, normal))/denominator if denominator else 0
            if not math.isfinite(alignment) or alignment < 0.99:
                _fail("patch crosses excessive curvature")
        for _, nodes in triangles + [(p['surface'], tuple(p['nodes'])) for p in proposals]:
            other = tuple(xyz[n] for n in nodes)
            shared = [xyz[n] for n in cycle if n in nodes]
            if _intersects(points, other, shared):
                _fail("patch intersects an existing surface triangle")
        proposals.append({'surface': face, 'nodes': list(cycle), 'area_mm2': area,
                          'max_deviation_mm': max(distances)})
    groups = physical_groups(gmsh)
    for patch in proposals:
        gmsh.model.mesh.addElementsByType(patch['surface'], 2, [], patch['nodes'])
    if _loops(_edges(_triangles(gmsh))):
        _fail("boundary remains open after repair")
    if physical_groups(gmsh) != groups:
        _fail("physical groups changed")
    return proposals


def validate_repaired_volume(gmsh, expected_groups):
    """Check the actual tetrahedral boundary, not only the surface mesh."""
    counts = Counter()
    all_tags = []
    for _, volume in gmsh.model.getEntities(3):
        types, tags, connectivity = gmsh.model.mesh.getElements(3, volume)
        for kind, element_tags, nodes in zip(types, tags, connectivity):
            if int(kind) != 4:
                _fail("repaired export requires first-order tetrahedra")
            all_tags.extend(map(int, element_tags))
            for i in range(0, len(nodes), 4):
                a, b, c, d = map(int, nodes[i:i+4])
                for facet in ((a,b,c), (a,b,d), (a,c,d), (b,c,d)):
                    counts[tuple(sorted(facet))] += 1
    if not all_tags or any(n > 2 for n in counts.values()):
        _fail("missing or non-manifold tetrahedra")
    qualities = gmsh.model.mesh.getElementQualities(all_tags, 'minSICN')
    if len(qualities) != len(all_tags) or any(not math.isfinite(q) or q <= 0 for q in qualities):
        _fail("inverted or degenerate tetrahedra")
    triangles = _triangles(gmsh)
    surface = {tuple(sorted(nodes)) for _, nodes in triangles}
    boundary = {facet for facet, count in counts.items() if count == 1}
    if len(surface) != len(triangles) or surface != boundary:
        _fail("tetrahedral boundary does not match the repaired surface mesh")
    if _loops(_edges(triangles)) or physical_groups(gmsh) != expected_groups:
        _fail("boundary topology or physical groups changed during meshing")
