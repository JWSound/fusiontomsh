import gmsh_support


DEFAULT_SURFACE_BLEND_FACTOR = 3.0


def _entity_key(dim_tag):
    return (int(dim_tag[0]), int(dim_tag[1]))


def _gmsh_point_from_fusion(point):
    scale = gmsh_support.FUSION_TO_GMSH_LENGTH_SCALE
    return (float(point.x) * scale, float(point.y) * scale, float(point.z) * scale)


def fusion_face_descriptor(face):
    point = getattr(face, "pointOnFace", None)
    area = getattr(face, "area", None)
    bbox = getattr(face, "boundingBox", None)

    normal = None
    if point is not None:
        try:
            ok, face_normal = face.evaluator.getNormalAtPoint(point)
            if ok:
                normal = (float(face_normal.x), float(face_normal.y), float(face_normal.z))
        except Exception:
            normal = None

    bbox_tuple = None
    if bbox is not None:
        try:
            min_pt = _gmsh_point_from_fusion(bbox.minPoint)
            max_pt = _gmsh_point_from_fusion(bbox.maxPoint)
            bbox_tuple = min_pt + max_pt
        except Exception:
            bbox_tuple = None

    return {
        "area": float(area) * gmsh_support.FUSION_TO_GMSH_LENGTH_SCALE ** 2 if area is not None else None,
        "centroid": _gmsh_point_from_fusion(point) if point is not None else None,
        "normal": normal,
        "bbox": bbox_tuple,
    }


def _distance_squared(a, b):
    return sum((float(a[idx]) - float(b[idx])) ** 2 for idx in range(3))


def _surface_descriptor(gmsh_module, surface_tag):
    bbox = gmsh_module.model.getBoundingBox(2, surface_tag)
    return {
        "tag": surface_tag,
        "area": float(gmsh_module.model.occ.getMass(2, surface_tag)),
        "centroid": tuple(float(v) for v in gmsh_module.model.occ.getCenterOfMass(2, surface_tag)),
        "bbox": tuple(float(v) for v in bbox),
    }


def _bbox_diagonal_squared(bbox):
    if not bbox:
        return 1.0
    return max(_distance_squared(bbox[:3], bbox[3:]), 1e-12)


def match_fusion_face_to_surfaces(face_descriptor, surface_descriptors):
    if not face_descriptor.get("centroid") or face_descriptor.get("area") is None:
        return []

    face_area = max(float(face_descriptor["area"]), 1e-12)
    face_centroid = face_descriptor["centroid"]
    face_bbox = face_descriptor.get("bbox")
    face_diag_sq = _bbox_diagonal_squared(face_bbox)
    scored = []

    for surface in surface_descriptors:
        area_error = abs(surface["area"] - face_area) / face_area
        centroid_error = _distance_squared(surface["centroid"], face_centroid) / face_diag_sq
        bbox_error = 0.0
        if face_bbox and surface.get("bbox"):
            bbox_error = sum(abs(surface["bbox"][idx] - face_bbox[idx]) for idx in range(6)) / max(face_area ** 0.5, 1e-6)

        score = area_error + centroid_error + 0.05 * bbox_error
        scored.append((score, area_error, centroid_error, surface["tag"]))

    if not scored:
        return []

    scored.sort()
    best_score, best_area_error, best_centroid_error, best_tag = scored[0]
    if best_area_error <= 0.02 and best_centroid_error <= 0.01:
        return [best_tag]
    if best_score <= 0.08:
        return [best_tag]
    return []


def _boundary_surfaces_for_volumes(gmsh_module, volume_tags):
    surfaces = set()
    for volume_tag in volume_tags:
        for dim, tag in gmsh_module.model.getBoundary(
            [(3, volume_tag)],
            combined=False,
            oriented=False,
            recursive=False
        ):
            if dim == 2:
                surfaces.add(tag)
    return sorted(surfaces)


def _add_surface_size_fields(gmsh_module, boundary_groups, default_size):
    field_ids = []
    default_lc = gmsh_support.gmsh_length(default_size)

    for boundary_group in boundary_groups:
        surface_tags = boundary_group.get("surface_tags", [])
        size = boundary_group.get("size")
        if not surface_tags or size is None:
            continue

        target_lc = gmsh_support.gmsh_length(size)
        blend_dist = max(default_lc, target_lc) * DEFAULT_SURFACE_BLEND_FACTOR

        distance_field = gmsh_module.model.mesh.field.add("Distance")
        gmsh_module.model.mesh.field.setNumbers(distance_field, "FacesList", sorted(surface_tags))
        gmsh_module.model.mesh.field.setNumber(distance_field, "Sampling", 100)

        threshold_field = gmsh_module.model.mesh.field.add("Threshold")
        gmsh_module.model.mesh.field.setNumber(threshold_field, "InField", distance_field)
        gmsh_module.model.mesh.field.setNumber(threshold_field, "LcMin", target_lc)
        gmsh_module.model.mesh.field.setNumber(threshold_field, "LcMax", default_lc)
        gmsh_module.model.mesh.field.setNumber(threshold_field, "DistMin", 0.0)
        gmsh_module.model.mesh.field.setNumber(threshold_field, "DistMax", max(blend_dist, 1e-6))
        field_ids.append(threshold_field)

    if not field_ids:
        return

    if len(field_ids) == 1:
        gmsh_module.model.mesh.field.setAsBackgroundMesh(field_ids[0])
    else:
        min_field = gmsh_module.model.mesh.field.add("Min")
        gmsh_module.model.mesh.field.setNumbers(min_field, "FieldsList", field_ids)
        gmsh_module.model.mesh.field.setAsBackgroundMesh(min_field)


def build_fem_export_model(gmsh_module, step_path, volume_name, default_size, boundary_groups):
    gmsh_module.model.add("FusionFEMExport")
    imported_entities = gmsh_module.model.occ.importShapes(step_path)
    gmsh_module.model.occ.synchronize()

    volume_tags = sorted(tag for dim, tag in imported_entities if dim == 3)
    if not volume_tags:
        volume_tags = sorted(tag for _, tag in gmsh_module.model.getEntities(3))
    if not volume_tags:
        raise RuntimeError("Gmsh did not import a closed volume from the selected body.")

    volume_group = gmsh_module.model.addPhysicalGroup(3, volume_tags)
    gmsh_module.model.setPhysicalName(3, volume_group, volume_name)

    boundary_surfaces = _boundary_surfaces_for_volumes(gmsh_module, volume_tags)
    surface_descriptors = [
        _surface_descriptor(gmsh_module, surface_tag)
        for surface_tag in boundary_surfaces
    ]

    unmatched_groups = []
    tagged_surface_tags = set()
    for boundary_group_data in boundary_groups:
        matched_surface_tags = set()
        for face_descriptor in boundary_group_data.get("face_descriptors", []):
            matched_surface_tags.update(match_fusion_face_to_surfaces(face_descriptor, surface_descriptors))

        if not matched_surface_tags:
            unmatched_groups.append(boundary_group_data.get("name", "Boundary"))
            boundary_group_data["surface_tags"] = []
            continue

        surface_tags = sorted(matched_surface_tags)
        boundary_group_data["surface_tags"] = surface_tags
        tagged_surface_tags.update(surface_tags)
        physical_group = gmsh_module.model.addPhysicalGroup(2, surface_tags)
        gmsh_module.model.setPhysicalName(2, physical_group, boundary_group_data["name"])

    untagged_boundary_surfaces = [
        surface_tag
        for surface_tag in boundary_surfaces
        if surface_tag not in tagged_surface_tags
    ]
    if untagged_boundary_surfaces:
        boundary_group = gmsh_module.model.addPhysicalGroup(2, untagged_boundary_surfaces)
        gmsh_module.model.setPhysicalName(2, boundary_group, f"{volume_name}_boundary")

    _add_surface_size_fields(gmsh_module, boundary_groups, default_size)
    return {
        "volume_tags": volume_tags,
        "boundary_surfaces": untagged_boundary_surfaces,
        "tagged_boundary_surfaces": sorted(tagged_surface_tags),
        "unmatched_groups": unmatched_groups,
    }
