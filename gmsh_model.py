import gmsh_support


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

    if enable_seam_fields:
        shared_curves = collect_shared_curve_adjacency(gmsh_module, body_surfaces)
        seam_targets_by_body = {}
        for curve_tag, body_indices in shared_curves.items():
            for body_idx in body_indices:
                if body_idx not in body_surfaces:
                    continue

                body_lc = gmsh_support.gmsh_length(body_settings[body_idx])
                finer_adjacent_sizes = [
                    gmsh_support.gmsh_length(body_settings[idx])
                    for idx in body_indices
                    if idx != body_idx and gmsh_support.gmsh_length(body_settings[idx]) < body_lc
                ]
                if not finer_adjacent_sizes:
                    continue

                seam_lc_min = min(finer_adjacent_sizes)
                seam_targets_by_body.setdefault(body_idx, {}).setdefault(seam_lc_min, set()).add(curve_tag)

        for body_idx, seam_targets in seam_targets_by_body.items():
            body_lc = gmsh_support.gmsh_length(body_settings[body_idx])
            for seam_lc_min, curve_tags in seam_targets.items():
                blend_dist = max(body_lc, seam_lc_min) * DEFAULT_SEAM_BLEND_FACTOR

                seam_field = add_distance_threshold_field(
                    gmsh_module,
                    "CurvesList",
                    curve_tags,
                    seam_lc_min,
                    body_lc,
                    0.0,
                    blend_dist,
                    body_surfaces[body_idx]
                )
                if seam_field:
                    field_ids.append(seam_field)

    if field_ids:
        if len(field_ids) == 1:
            gmsh_module.model.mesh.field.setAsBackgroundMesh(field_ids[0])
        else:
            min_field = gmsh_module.model.mesh.field.add("Min")
            gmsh_module.model.mesh.field.setNumbers(min_field, "FieldsList", field_ids)
            gmsh_module.model.mesh.field.setAsBackgroundMesh(min_field)


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
        mapped_body_entities = fragment_body_entities(gmsh_module, body_dim_tags)
        gmsh_module.model.occ.synchronize()

        for body_idx, group_name in enumerate(group_name_map):
            surfaces = boundary_surfaces(gmsh_module, mapped_body_entities.get(body_idx, []))
            if not surfaces:
                surfaces = boundary_surfaces(gmsh_module, body_dim_tags[body_idx])

            if surfaces:
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
