import heapq
import math

import gmsh_support
import seam_repair


DEFAULT_SEAM_BLEND_FACTOR = 3.0


def entity_key(dim_tag):
    return (int(dim_tag[0]), int(dim_tag[1]))


def entity_exists(existing_entities, dim_tag):
    return entity_key(dim_tag) in existing_entities


def boundary_surfaces(gmsh_module, dim_tags):
    surfaces = set()
    for dim, tag in dim_tags:
        if dim == 2:
            surfaces.add(tag)
        elif dim == 3:
            try:
                for boundary_dim, boundary_tag in gmsh_module.model.getBoundary(
                    [(dim, tag)],
                    combined=False,
                    oriented=False,
                    recursive=False
                ):
                    if boundary_dim == 2:
                        surfaces.add(boundary_tag)
            except Exception:
                pass
    return sorted(surfaces)


def fragment_body_entities(gmsh_module, body_dim_tags):
    input_records = []
    all_dim_tags = []
    for body_idx, dim_tags in enumerate(body_dim_tags):
        for dim_tag in dim_tags:
            normalized = entity_key(dim_tag)
            input_records.append((body_idx, normalized))
            all_dim_tags.append(normalized)

    if len(all_dim_tags) < 2:
        return {idx: list(dim_tags) for idx, dim_tags in enumerate(body_dim_tags)}

    try:
        _, out_dim_tags_map = gmsh_module.model.occ.fragment(
            [all_dim_tags[0]],
            all_dim_tags[1:],
            removeObject=True,
            removeTool=True
        )
    except Exception:
        gmsh_module.model.occ.synchronize()
        return {idx: list(dim_tags) for idx, dim_tags in enumerate(body_dim_tags)}

    mapped_by_body = {idx: [] for idx in range(len(body_dim_tags))}
    for map_idx, mapped_entities in enumerate(out_dim_tags_map):
        if map_idx >= len(input_records):
            continue
        body_idx, _ = input_records[map_idx]
        for dim_tag in mapped_entities:
            mapped_by_body[body_idx].append(entity_key(dim_tag))

    gmsh_module.model.occ.synchronize()
    existing_entities = set(entity_key(dim_tag) for dim_tag in gmsh_module.model.getEntities())
    for body_idx, mapped_entities in mapped_by_body.items():
        deduped = []
        seen = set()
        for dim_tag in mapped_entities:
            if dim_tag in seen or not entity_exists(existing_entities, dim_tag):
                continue
            seen.add(dim_tag)
            deduped.append(dim_tag)
        mapped_by_body[body_idx] = deduped

    return mapped_by_body


def collect_shared_curve_adjacency(gmsh_module, body_surfaces):
    curve_to_bodies = {}
    for body_idx, surfaces in body_surfaces.items():
        for surface_tag in surfaces:
            try:
                boundary = gmsh_module.model.getBoundary(
                    [(2, surface_tag)],
                    combined=False,
                    oriented=False,
                    recursive=False
                )
            except Exception:
                continue

            for dim, curve_tag in boundary:
                if dim != 1:
                    continue
                curve_to_bodies.setdefault(curve_tag, set()).add(body_idx)

    return {
        curve_tag: sorted(body_indices)
        for curve_tag, body_indices in curve_to_bodies.items()
        if len(body_indices) > 1
    }


def body_boundary_curves(gmsh_module, surfaces):
    # Keep individual face boundaries, including edges of any sliver faces
    # produced by fragmentation of an imperfectly coincident interface.
    return {
        tag
        for dim, tag in gmsh_module.model.getBoundary(
            [(2, tag) for tag in surfaces],
            combined=False, oriented=False, recursive=False
        )
        if dim == 1
    }


def add_distance_threshold_field(gmsh_module, entity_option, entity_tags, lc_min, lc_max, dist_min, dist_max, surfaces=None):
    if not entity_tags:
        return None

    distance_field = gmsh_module.model.mesh.field.add("Distance")
    gmsh_module.model.mesh.field.setNumbers(distance_field, entity_option, sorted(entity_tags))
    gmsh_module.model.mesh.field.setNumber(distance_field, "Sampling", 100)

    threshold_field = gmsh_module.model.mesh.field.add("Threshold")
    gmsh_module.model.mesh.field.setNumber(threshold_field, "InField", distance_field)
    gmsh_module.model.mesh.field.setNumber(threshold_field, "LcMin", lc_min)
    gmsh_module.model.mesh.field.setNumber(threshold_field, "LcMax", lc_max)
    gmsh_module.model.mesh.field.setNumber(threshold_field, "DistMin", dist_min)
    gmsh_module.model.mesh.field.setNumber(threshold_field, "DistMax", max(dist_max, 1e-6))

    if surfaces is None:
        return threshold_field

    restrict_field = gmsh_module.model.mesh.field.add("Restrict")
    gmsh_module.model.mesh.field.setNumber(restrict_field, "InField", threshold_field)
    gmsh_module.model.mesh.field.setNumbers(restrict_field, "SurfacesList", sorted(surfaces))
    return restrict_field


def add_background_mesh_fields(gmsh_module, body_surfaces, body_settings, enable_seam_fields):
    field_ids = []

    for body_idx, surfaces in body_surfaces.items():
        body_size = body_settings[body_idx]
        body_field = add_distance_threshold_field(
            gmsh_module,
            "FacesList",
            surfaces,
            gmsh_support.gmsh_length(body_size),
            gmsh_support.gmsh_length(body_size),
            0.0,
            gmsh_support.gmsh_length(body_size),
            surfaces
        )
        if body_field:
            field_ids.append(body_field)

    set_background_fields(gmsh_module, field_ids)
    if enable_seam_fields:
        targets = collect_seam_targets(gmsh_module, body_surfaces, body_settings)
        if targets:
            field_ids.extend(add_surface_distance_fields(
                gmsh_module, body_surfaces, body_settings, targets
            ))
    set_background_fields(gmsh_module, field_ids)


def set_background_fields(gmsh_module, field_ids):
    if field_ids:
        if len(field_ids) == 1:
            gmsh_module.model.mesh.field.setAsBackgroundMesh(field_ids[0])
        else:
            min_field = gmsh_module.model.mesh.field.add("Min")
            gmsh_module.model.mesh.field.setNumbers(min_field, "FieldsList", field_ids)
            gmsh_module.model.mesh.field.setAsBackgroundMesh(min_field)


def collect_seam_targets(gmsh_module, body_surfaces, body_settings):
    """Seed only real shared edges; proximity must never establish connectivity."""
    targets = {}
    for curve, bodies in collect_shared_curve_adjacency(gmsh_module, body_surfaces).items():
        finest = min(gmsh_support.gmsh_length(body_settings[b]) for b in bodies)
        for body in bodies:
            if gmsh_support.gmsh_length(body_settings[body]) > finest:
                targets.setdefault(body, {}).setdefault(finest, set()).add(curve)
    return targets


def surface_distances(points, triangles, seeds, limit):
    """Truncated multi-source Dijkstra on a body's pilot triangulation.

    Nodes are identified by topology, never merged by coordinate proximity.
    Edge paths approximate geodesics (with mesh-direction dependent error).
    """
    graph = {}
    for triangle in triangles:
        for a, b in zip(triangle, triangle[1:] + triangle[:1]):
            length = math.dist(points[a], points[b])
            graph.setdefault(a, {})[b] = length
            graph.setdefault(b, {})[a] = length
    distances = {node: 0.0 for node in seeds if node in graph}
    queue = [(0.0, node) for node in distances]
    heapq.heapify(queue)
    while queue:
        distance, node = heapq.heappop(queue)
        if distance != distances[node]:
            continue
        for neighbor, length in graph[node].items():
            candidate = distance + length
            if candidate < limit and candidate < distances.get(neighbor, math.inf):
                distances[neighbor] = candidate
                heapq.heappush(queue, (candidate, neighbor))
    return distances


def add_surface_distance_fields(gmsh_module, body_surfaces, body_settings, targets):
    """Build independent per-face views, then discard the preliminary mesh.

    List-based views survive mesh.clear(). Restrict each view to its CAD face
    so a nearby panel can never supply the interpolation for another panel.
    The pilot uses quarter-size triangles, independent of final algorithm.
    """
    mesh = gmsh_module.model.mesh
    options = {
        "Mesh.MeshSizeFactor": 0.25,
        "Mesh.MeshSizeMin": 0,
        "Mesh.MeshSizeMax": max(map(gmsh_support.gmsh_length, body_settings)),
        "Mesh.MeshSizeFromPoints": 0,
        "Mesh.MeshSizeFromCurvature": 0,
        "Mesh.MeshSizeExtendFromBoundary": 0,
        "Mesh.Algorithm": 6,
        "Mesh.ElementOrder": 1,
        "Mesh.RecombineAll": 0,
    }
    saved = {key: gmsh_module.option.getNumber(key) for key in options}
    fields = []
    try:
        for key, value in options.items():
            gmsh_module.option.setNumber(key, value)
        mesh.generate(2)
        tags, coords, _ = mesh.getNodes()
        points = {int(tag): tuple(coords[3*i:3*i+3]) for i, tag in enumerate(tags)}
        for body, source_groups in targets.items():
            coarse = gmsh_support.gmsh_length(body_settings[body])
            limit = coarse * DEFAULT_SEAM_BLEND_FACTOR
            by_face = {}
            for face in body_surfaces[body]:
                types, _, connectivity = mesh.getElements(2, face)
                triangles = []
                for kind, nodes in zip(types, connectivity):
                    if kind != 2:
                        raise RuntimeError("Surface-distance pilot requires linear triangles")
                    triangles.extend(tuple(map(int, nodes[i:i+3]))
                                     for i in range(0, len(nodes), 3))
                if not triangles:
                    raise RuntimeError("Surface-distance pilot has an unmeshed face")
                by_face[face] = triangles
            triangles = [tri for tris in by_face.values() for tri in tris]
            sizes = {node: coarse for tri in triangles for node in tri}
            for fine, curves in source_groups.items():
                seeds = set()
                for curve in curves:
                    nodes, _, _ = mesh.getNodes(1, curve, includeBoundary=True)
                    seeds.update(map(int, nodes))
                distances = surface_distances(points, triangles, seeds, limit)
                if not distances:
                    raise RuntimeError("Surface-distance pilot lost a shared seam")
                for node, distance in distances.items():
                    sizes[node] = min(sizes[node], fine + (coarse-fine)*distance/limit)
            for face, tris in by_face.items():
                data = []
                for tri in tris:
                    data.extend(points[node][axis] for axis in range(3) for node in tri)
                    data.extend(sizes[node] for node in tri)
                view = gmsh_module.view.add("Surface seam sizes %s" % face)
                gmsh_module.view.addListData(view, "ST", len(tris), data)
                gmsh_module.view.option.setNumber(view, "Visible", 0)
                field = mesh.field.add("PostView")
                mesh.field.setNumber(field, "ViewTag", view)
                mesh.field.setNumber(field, "UseClosest", 1)
                restricted = mesh.field.add("Restrict")
                mesh.field.setNumber(restricted, "InField", field)
                mesh.field.setNumbers(restricted, "SurfacesList", [face])
                fields.append(restricted)
    finally:
        mesh.clear()
        for key, value in saved.items():
            gmsh_module.option.setNumber(key, value)
    return fields


def build_gmsh_export_model(gmsh_module, step_paths, group_name_map, body_settings, conformal_topology=True):
    gmsh_module.model.add("FusionExport")

    body_surfaces = {}

    if conformal_topology:
        body_dim_tags = []
        for step_path in step_paths:
            imported_entities = gmsh_module.model.occ.importShapes(step_path)
            body_dim_tags.append([
                entity_key(dim_tag)
                for dim_tag in imported_entities
                if dim_tag[0] in (2, 3)
            ])

        gmsh_module.model.occ.synchronize()
        body_dim_tags, repaired_normals = seam_repair.repair_planar_caps(gmsh_module, body_dim_tags)
        mapped_body_entities = fragment_body_entities(gmsh_module, body_dim_tags)
        gmsh_module.model.occ.synchronize()

        for body_idx, group_name in enumerate(group_name_map):
            surfaces = boundary_surfaces(gmsh_module, mapped_body_entities.get(body_idx, []))
            if not surfaces:
                surfaces = boundary_surfaces(gmsh_module, body_dim_tags[body_idx])

            if surfaces:
                if body_idx in repaired_normals:
                    seam_repair.restore_cap_orientation(gmsh_module, surfaces, repaired_normals[body_idx])
                body_surfaces[body_idx] = surfaces
                physical_tag = gmsh_module.model.addPhysicalGroup(2, surfaces)
                gmsh_module.model.setPhysicalName(2, physical_tag, group_name)
    else:
        for body_idx, (step_path, group_name) in enumerate(zip(step_paths, group_name_map)):
            existing_surfaces = set(tag for _, tag in gmsh_module.model.getEntities(2))
            gmsh_module.model.occ.importShapes(step_path)
            gmsh_module.model.occ.synchronize()
            all_surfaces = set(tag for _, tag in gmsh_module.model.getEntities(2))
            new_surfaces = sorted(list(all_surfaces - existing_surfaces))

            if new_surfaces:
                body_surfaces[body_idx] = new_surfaces
                physical_tag = gmsh_module.model.addPhysicalGroup(2, new_surfaces)
                gmsh_module.model.setPhysicalName(2, physical_tag, group_name)

    add_background_mesh_fields(
        gmsh_module,
        body_surfaces,
        body_settings,
        enable_seam_fields=conformal_topology
    )
