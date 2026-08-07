import os
import tempfile
from datetime import datetime
import time

from export_types import BodyMeshSettings, ExportBody
import fem_model
import fusion_export
import gmsh_model
import gmsh_support
from msh_settings import algo_3d_text_to_id, algo_text_to_id, load_mesh_settings, sanitize_body_size_settings, save_fem_settings, save_mesh_settings


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


def export_fem_body_to_msh(
    gmsh_module,
    design,
    body,
    body_name,
    msh_path,
    default_size,
    algo_3d_text,
    boundary_groups,
    settings_path,
    body_preset_key=None,
):
    workflow_started = time.perf_counter()
    if body is None:
        return {
            "success": False,
            "message": "Select one solid body before exporting a FEM mesh.",
        }

    if not fusion_export.is_solid_body(body):
        return {
            "success": False,
            "message": "The selected target body is not a solid/watertight body.",
        }

    default_size, _ = sanitize_body_size_settings(default_size, 0)
    cleaned_boundary_groups = []
    global_min_val = default_size
    global_max_val = default_size

    for boundary_group in boundary_groups:
        group_name = boundary_group.get("name", "").strip()
        face_descriptors = boundary_group.get("face_descriptors", [])
        if not group_name or not face_descriptors:
            continue

        group_size, _ = sanitize_body_size_settings(boundary_group.get("size", default_size), 0)
        global_min_val = min(global_min_val, group_size)
        global_max_val = max(global_max_val, group_size)
        cleaned_boundary_groups.append({
            "name": group_name,
            "size": group_size,
            "face_descriptors": face_descriptors,
            "face_tokens": [
                token
                for token in boundary_group.get("face_tokens", [])
                if isinstance(token, str) and token
            ],
        })

    temp_dir = tempfile.gettempdir()
    step_path = os.path.join(temp_dir, "fusion_fem_export_temp.step")
    export_mgr = design.exportManager
    mesh_info = {}

    try:
        phase_started = time.perf_counter()
        fusion_export.export_body_to_step(design, export_mgr, body, step_path)
        fusion_step_export_s = time.perf_counter() - phase_started

        def build_model():
            mesh_info.update(fem_model.build_fem_export_model(
                gmsh_module,
                step_path,
                body_name,
                default_size,
                cleaned_boundary_groups
            ))

        mesh_statistics = gmsh_support.write_gmsh_mesh(
            gmsh_module,
            msh_path,
            build_model,
            global_min_val,
            global_max_val,
            0,
            algo_id=None,
            mesh_dimension=3,
            msh_file_version=4.1,
            algo_3d_id=algo_3d_text_to_id(algo_3d_text),
        )

        fem_settings = load_mesh_settings(settings_path).get("fem", {})
        by_body = dict(fem_settings.get("by_body", {}))
        try:
            body_token = body.entityToken
        except Exception:
            body_token = ""
        preset_key = body_preset_key or body_token
        if preset_key and body_token:
            by_body[preset_key] = {
                "body_token": body_token,
                "body_name": body_name,
                "msh_path": msh_path,
                "algo_3d": algo_3d_text,
                "default_size": default_size,
                "boundary_groups": [
                    {
                        "name": group["name"],
                        "size": group["size"],
                        "face_tokens": group.get("face_tokens", []),
                    }
                    for group in cleaned_boundary_groups
                ],
            }

        phase_started = time.perf_counter()
        save_fem_settings(settings_path, {
            "last_msh_path": msh_path,
            "last_body_key": preset_key,
            "algo_3d": algo_3d_text,
            "default_size": default_size,
            "boundary_size": (
                cleaned_boundary_groups[0]["size"]
                if cleaned_boundary_groups
                else default_size
            ),
            "by_body": by_body,
        })
        settings_save_s = time.perf_counter() - phase_started
    finally:
        if os.path.exists(step_path):
            os.remove(step_path)

    timings = dict(mesh_statistics.get("timings", {}))
    timings.update(mesh_info.get("timings", {}))
    timings["fusion_step_export_s"] = fusion_step_export_s
    timings["settings_save_s"] = settings_save_s
    timings["workflow_total_s"] = time.perf_counter() - workflow_started

    return {
        "success": True,
        "msh_path": msh_path,
        "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "unmatched_groups": mesh_info.get("unmatched_groups", []),
        "volume_count": len(mesh_info.get("volume_tags", [])),
        "boundary_surface_count": len(mesh_info.get("boundary_surfaces", [])),
        "group_count": mesh_statistics.get("physical_group_count", 0),
        "element_count": mesh_statistics.get("element_count", 0),
        "timings": timings,
    }
