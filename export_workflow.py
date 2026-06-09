import os
import tempfile

from export_types import BodyMeshSettings, ExportBody
import fusion_export
import gmsh_model
import gmsh_support
from msh_settings import algo_text_to_id, sanitize_body_size_settings, save_mesh_settings


def _write_gmsh_mesh(
    gmsh_module,
    msh_path,
    step_paths,
    group_name_map,
    body_settings,
    global_min_val,
    global_max_val,
    effective_global_curvature,
    algo_id,
    conformal_topology=True
):
    def build_model():
        gmsh_model.build_gmsh_export_model(
            gmsh_module,
            step_paths,
            group_name_map,
            body_settings,
            conformal_topology=conformal_topology
        )

    gmsh_support.write_gmsh_mesh(
        gmsh_module,
        msh_path,
        build_model,
        global_min_val,
        global_max_val,
        effective_global_curvature,
        algo_id,
    )


def export_visible_bodies_to_msh(
    gmsh_module,
    design,
    msh_path,
    algo_text,
    seam_blending_enabled,
    body_setting_resolver,
    settings_path,
):
    visible_bodies = fusion_export.collect_visible_bodies(design)
    if not visible_bodies:
        return {
            "success": False,
            "message": "No visible bodies found to export.",
        }

    temp_dir = tempfile.gettempdir()
    export_bodies = []
    export_mgr = design.exportManager
    used_group_names = set()
    settings_by_body = {}

    global_min_val = None
    global_max_val = None
    effective_global_curvature = 0

    for idx, body_info in enumerate(visible_bodies, start=1):
        body = body_info[0]
        raw_name = body_info[1]
        unique_name = fusion_export.unique_group_name(raw_name, used_group_names)

        body_size, body_curvature = body_setting_resolver(idx, unique_name)
        body_size, body_curvature = sanitize_body_size_settings(body_size, body_curvature)

        if global_min_val is None or body_size < global_min_val:
            global_min_val = body_size
        if global_max_val is None or body_size > global_max_val:
            global_max_val = body_size
        if body_curvature > effective_global_curvature:
            effective_global_curvature = body_curvature

        step_path = os.path.join(temp_dir, f'fusion_export_temp_{idx}.step')
        fusion_export.export_body_to_step(design, export_mgr, body, step_path)
        mesh_settings = BodyMeshSettings(body_size, body_curvature)
        export_bodies.append(ExportBody(body, unique_name, mesh_settings, step_path))
        settings_by_body[unique_name] = {
            "size": body_size,
            "curvature": body_curvature,
        }

    used_conformal_topology = seam_blending_enabled
    conformal_error = None

    try:
        step_paths = [export_body.step_path for export_body in export_bodies]
        group_name_map = [export_body.group_name for export_body in export_bodies]
        body_settings = [
            export_body.settings.size
            for export_body in export_bodies
        ]

        if seam_blending_enabled:
            try:
                _write_gmsh_mesh(
                    gmsh_module,
                    msh_path,
                    step_paths,
                    group_name_map,
                    body_settings,
                    global_min_val,
                    global_max_val,
                    effective_global_curvature,
                    algo_text_to_id(algo_text),
                    conformal_topology=True
                )
            except Exception as mesh_error:
                conformal_error = mesh_error
                used_conformal_topology = False
                _write_gmsh_mesh(
                    gmsh_module,
                    msh_path,
                    step_paths,
                    group_name_map,
                    body_settings,
                    global_min_val,
                    global_max_val,
                    effective_global_curvature,
                    algo_text_to_id(algo_text),
                    conformal_topology=False
                )
        else:
            _write_gmsh_mesh(
                gmsh_module,
                msh_path,
                step_paths,
                group_name_map,
                body_settings,
                global_min_val,
                global_max_val,
                effective_global_curvature,
                algo_text_to_id(algo_text),
                conformal_topology=False
            )

        defaults_size, defaults_curvature = sanitize_body_size_settings(
            global_min_val,
            effective_global_curvature
        )
        save_mesh_settings(settings_path, {
            "last_msh_path": msh_path,
            "algo_2d": algo_text,
            "defaults": {
                "size": defaults_size,
                "curvature": defaults_curvature,
            },
            "seam_blending": seam_blending_enabled,
            "by_body": settings_by_body,
        })
    finally:
        for export_body in export_bodies:
            if os.path.exists(export_body.step_path):
                os.remove(export_body.step_path)

    return {
        "success": True,
        "used_conformal_topology": used_conformal_topology,
        "conformal_error": conformal_error,
        "msh_path": msh_path,
    }
