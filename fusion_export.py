import adsk.core


def is_visible_body(body):
    try:
        return body.isVisible
    except Exception:
        try:
            return body.isLightBulbOn
        except Exception:
            return False


def collect_visible_bodies(design):
    root_comp = design.rootComponent
    visible_bodies = []

    for body in root_comp.bRepBodies:
        if is_visible_body(body):
            visible_bodies.append((body, body.name or "Body"))

    for occurrence in root_comp.allOccurrences:
        occurrence_name = occurrence.fullPathName if occurrence.fullPathName else occurrence.name
        for body in occurrence.bRepBodies:
            if is_visible_body(body):
                body_name = body.name if body.name else "Body"
                visible_bodies.append((body, f"{occurrence_name}:{body_name}"))

    return visible_bodies


def unique_group_name(base_name, existing_names):
    if base_name not in existing_names:
        existing_names.add(base_name)
        return base_name

    suffix = 2
    while f"{base_name}_{suffix}" in existing_names:
        suffix += 1

    unique_name = f"{base_name}_{suffix}"
    existing_names.add(unique_name)
    return unique_name


def export_body_to_step(design, export_mgr, body, step_path):
    try:
        step_options = export_mgr.createSTEPExportOptions(step_path, body)
        export_mgr.execute(step_options)
        return
    except Exception:
        pass

    temp_occ = None
    try:
        transform = adsk.core.Matrix3D.create()
        temp_occ = design.rootComponent.occurrences.addNewComponent(transform)
        body.copyToComponent(temp_occ)
        step_options = export_mgr.createSTEPExportOptions(step_path, temp_occ.component)
        export_mgr.execute(step_options)
    finally:
        if temp_occ:
            try:
                temp_occ.deleteMe()
            except Exception:
                pass
