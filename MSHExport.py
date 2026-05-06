"""
Autodesk Fusion script for exporting visible bodies to configurable Gmsh
.msh meshes for downstream BEM/FEM analysis.

This module creates a Fusion command that:
- Detects visible bodies (solid and non-solid) in the active design.
- Exports each selected body to temporary STEP geometry.
- Builds and meshes geometry with Gmsh using a user-selected 2D algorithm.
- Supports per-body meshing controls (minimum size, maximum size, and
    curvature-based sizing weight) to tune mesh density by region.
- Writes mesh output as .msh files suitable for third-party BEM/FEM simulation tools.

Dependency loading strategy:
- Loads bundled gmsh wheel files in a local wheelhouse directory.

This script is intended to streamline mesh generation directly from Fusion,
reducing manual export/remeshing steps before external electromagnetic,
acoustic, or structural analyses.
"""

import adsk.core, adsk.fusion, adsk.cam, traceback
import os
import tempfile
import sys
import glob
import zipfile
import shutil
import json
import platform


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WHEELHOUSE_DIR = os.path.join(SCRIPT_DIR, "wheelhouse")
WHEEL_EXTRACT_DIR = os.path.join(SCRIPT_DIR, ".gmsh_wheels")
SETTINGS_PATH = os.path.join(SCRIPT_DIR, ".msh_export_settings.json")
COMMAND_ID = "GmshExportCommand"

DEFAULT_BODY_MIN = 1.5
DEFAULT_BODY_MAX = 3.0
DEFAULT_BODY_CURVATURE = 0
DEFAULT_SEAM_BLENDING_ENABLED = True
DEFAULT_SEAM_BLEND_FACTOR = 3.0
BODY_TABLE_MAX_VISIBLE_ROWS = 8


def _sanitize_body_settings(min_val, max_val, curvature):
    safe_min = float(min_val) if min_val is not None else DEFAULT_BODY_MIN
    safe_max = float(max_val) if max_val is not None else DEFAULT_BODY_MAX
    safe_curvature = int(curvature) if curvature is not None else DEFAULT_BODY_CURVATURE

    if safe_min <= 0:
        safe_min = 1e-6
    if safe_max < safe_min:
        safe_max = safe_min
    if safe_curvature < 0:
        safe_curvature = 0
    if safe_curvature > 100:
        safe_curvature = 100

    return safe_min, safe_max, safe_curvature


def _load_mesh_settings():
    settings = {
        "defaults": {
            "min": DEFAULT_BODY_MIN,
            "max": DEFAULT_BODY_MAX,
            "curvature": DEFAULT_BODY_CURVATURE,
        },
        "seam_blending": DEFAULT_SEAM_BLENDING_ENABLED,
        "by_body": {}
    }

    if not os.path.isfile(SETTINGS_PATH):
        return settings

    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as settings_file:
            loaded = json.load(settings_file)

        defaults = loaded.get("defaults", {}) if isinstance(loaded, dict) else {}
        default_min, default_max, default_curvature = _sanitize_body_settings(
            defaults.get("min", DEFAULT_BODY_MIN),
            defaults.get("max", DEFAULT_BODY_MAX),
            defaults.get("curvature", DEFAULT_BODY_CURVATURE)
        )
        settings["defaults"] = {
            "min": default_min,
            "max": default_max,
            "curvature": default_curvature,
        }
        seam_blending = loaded.get("seam_blending", DEFAULT_SEAM_BLENDING_ENABLED) if isinstance(loaded, dict) else DEFAULT_SEAM_BLENDING_ENABLED
        settings["seam_blending"] = seam_blending if isinstance(seam_blending, bool) else DEFAULT_SEAM_BLENDING_ENABLED

        loaded_by_body = loaded.get("by_body", {}) if isinstance(loaded, dict) else {}
        if isinstance(loaded_by_body, dict):
            cleaned_by_body = {}
            for body_name, body_values in loaded_by_body.items():
                if not isinstance(body_name, str) or not isinstance(body_values, dict):
                    continue
                body_min, body_max, body_curvature = _sanitize_body_settings(
                    body_values.get("min", default_min),
                    body_values.get("max", default_max),
                    body_values.get("curvature", default_curvature)
                )
                cleaned_by_body[body_name] = {
                    "min": body_min,
                    "max": body_max,
                    "curvature": body_curvature,
                }
            settings["by_body"] = cleaned_by_body
    except Exception:
        return settings

    return settings


def _save_mesh_settings(settings_data):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as settings_file:
            json.dump(settings_data, settings_file, indent=2)
    except Exception:
        pass


def _merge_tree(src_dir, dst_dir):
    if not os.path.isdir(src_dir):
        return

    os.makedirs(dst_dir, exist_ok=True)
    for root, dir_names, file_names in os.walk(src_dir):
        rel_root = os.path.relpath(root, src_dir)
        target_root = dst_dir if rel_root == "." else os.path.join(dst_dir, rel_root)
        os.makedirs(target_root, exist_ok=True)

        for dir_name in dir_names:
            os.makedirs(os.path.join(target_root, dir_name), exist_ok=True)

        for file_name in file_names:
            source_file = os.path.join(root, file_name)
            target_file = os.path.join(target_root, file_name)
            if os.path.exists(target_file):
                continue
            try:
                shutil.copy2(source_file, target_file)
            except PermissionError:
                if not os.path.exists(target_file):
                    raise


def _normalize_gmsh_wheel_layout(extract_dir):
    normalized_marker = os.path.join(extract_dir, ".normalized")
    if os.path.isfile(normalized_marker):
        return

    data_roots = glob.glob(os.path.join(extract_dir, "*.data", "data"))
    for data_root in data_roots:
        _merge_tree(data_root, extract_dir)

    try:
        with open(normalized_marker, "w", encoding="utf-8") as marker_file:
            marker_file.write("ok")
    except Exception:
        pass


def _import_gmsh_from_wheelhouse():
    if not os.path.isdir(WHEELHOUSE_DIR):
        return None

    all_wheel_paths = sorted(
        glob.glob(os.path.join(WHEELHOUSE_DIR, "gmsh-*.whl")),
        reverse=True
    )

    machine = platform.machine().lower()
    wheel_paths = []
    fallback_wheels = []

    for wheel_path in all_wheel_paths:
        wheel_filename = os.path.basename(wheel_path).lower()

        if sys.platform.startswith("win"):
            if "win_amd64" in wheel_filename:
                wheel_paths.append(wheel_path)
            else:
                fallback_wheels.append(wheel_path)
        elif sys.platform == "darwin":
            if machine in ("arm64", "aarch64") and "macosx_12_0_arm64" in wheel_filename:
                wheel_paths.append(wheel_path)
            elif machine in ("x86_64", "amd64") and "macosx" in wheel_filename and "x86_64" in wheel_filename:
                wheel_paths.append(wheel_path)
            else:
                fallback_wheels.append(wheel_path)
        else:
            fallback_wheels.append(wheel_path)

    wheel_paths.extend(fallback_wheels)

    if not wheel_paths:
        return None

    os.makedirs(WHEEL_EXTRACT_DIR, exist_ok=True)

    for wheel_path in wheel_paths:
        wheel_name = os.path.splitext(os.path.basename(wheel_path))[0]
        extract_dir = os.path.join(WHEEL_EXTRACT_DIR, wheel_name)
        ready_marker = os.path.join(extract_dir, ".ready")

        if not os.path.isfile(ready_marker):
            if os.path.isdir(extract_dir):
                shutil.rmtree(extract_dir, ignore_errors=True)
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(wheel_path, "r") as wheel_zip:
                wheel_zip.extractall(extract_dir)
            with open(ready_marker, "w", encoding="utf-8") as marker_file:
                marker_file.write("ok")

        _normalize_gmsh_wheel_layout(extract_dir)

        if sys.platform.startswith("win") and hasattr(os, "add_dll_directory"):
            for dll_dir in (extract_dir, os.path.join(extract_dir, "lib"), os.path.join(extract_dir, "bin")):
                if os.path.isdir(dll_dir):
                    try:
                        os.add_dll_directory(dll_dir)
                    except Exception:
                        pass

        sys.path.insert(0, extract_dir)
        try:
            import gmsh as vendored_gmsh
            return vendored_gmsh
        except Exception:
            try:
                sys.path.remove(extract_dir)
            except ValueError:
                pass

    return None


gmsh = None

# Global variables to keep handlers in memory
handlers = []


def _cleanup_command_definition(ui=None):
    try:
        if ui is None:
            ui = adsk.core.Application.get().userInterface
        cmd_def = ui.commandDefinitions.itemById(COMMAND_ID)
        if cmd_def:
            cmd_def.deleteMe()
    except Exception:
        pass


def _finish_script(ui=None, terminate=True, cleanup_command=True):
    if cleanup_command:
        _cleanup_command_definition(ui)
    handlers.clear()
    if not terminate:
        return
    try:
        adsk.terminate()
    except Exception:
        pass


def _is_visible_body(body):
    try:
        return body.isVisible
    except:
        try:
            return body.isLightBulbOn
        except:
            return False


def _collect_visible_bodies(design):
    root_comp = design.rootComponent
    visible_bodies = []

    for body in root_comp.bRepBodies:
        if _is_visible_body(body):
            visible_bodies.append((body, body.name or "Body"))

    for occurrence in root_comp.allOccurrences:
        occurrence_name = occurrence.fullPathName if occurrence.fullPathName else occurrence.name
        for body in occurrence.bRepBodies:
            if _is_visible_body(body):
                body_name = body.name if body.name else "Body"
                visible_bodies.append((body, f"{occurrence_name}:{body_name}"))

    return visible_bodies


def _unique_group_name(base_name, existing_names):
    if base_name not in existing_names:
        existing_names.add(base_name)
        return base_name

    suffix = 2
    while f"{base_name}_{suffix}" in existing_names:
        suffix += 1

    unique_name = f"{base_name}_{suffix}"
    existing_names.add(unique_name)
    return unique_name


def _export_body_to_step(design, export_mgr, body, step_path):
    try:
        step_options = export_mgr.createSTEPExportOptions(step_path, body)
        export_mgr.execute(step_options)
        return
    except:
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
            except:
                pass


def _entity_key(dim_tag):
    return (int(dim_tag[0]), int(dim_tag[1]))


def _entity_exists(existing_entities, dim_tag):
    return _entity_key(dim_tag) in existing_entities


def _boundary_surfaces(dim_tags):
    surfaces = set()
    for dim, tag in dim_tags:
        if dim == 2:
            surfaces.add(tag)
        elif dim == 3:
            try:
                for boundary_dim, boundary_tag in gmsh.model.getBoundary(
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


def _fragment_body_entities(body_dim_tags):
    input_records = []
    all_dim_tags = []
    for body_idx, dim_tags in enumerate(body_dim_tags):
        for dim_tag in dim_tags:
            normalized = _entity_key(dim_tag)
            input_records.append((body_idx, normalized))
            all_dim_tags.append(normalized)

    if len(all_dim_tags) < 2:
        return {idx: list(dim_tags) for idx, dim_tags in enumerate(body_dim_tags)}

    try:
        _, out_dim_tags_map = gmsh.model.occ.fragment(
            [all_dim_tags[0]],
            all_dim_tags[1:],
            removeObject=True,
            removeTool=True
        )
    except Exception:
        gmsh.model.occ.synchronize()
        return {idx: list(dim_tags) for idx, dim_tags in enumerate(body_dim_tags)}

    mapped_by_body = {idx: [] for idx in range(len(body_dim_tags))}
    for map_idx, mapped_entities in enumerate(out_dim_tags_map):
        if map_idx >= len(input_records):
            continue
        body_idx, _ = input_records[map_idx]
        for dim_tag in mapped_entities:
            mapped_by_body[body_idx].append(_entity_key(dim_tag))

    gmsh.model.occ.synchronize()
    existing_entities = set(_entity_key(dim_tag) for dim_tag in gmsh.model.getEntities())
    for body_idx, mapped_entities in mapped_by_body.items():
        deduped = []
        seen = set()
        for dim_tag in mapped_entities:
            if dim_tag in seen or not _entity_exists(existing_entities, dim_tag):
                continue
            seen.add(dim_tag)
            deduped.append(dim_tag)
        mapped_by_body[body_idx] = deduped

    return mapped_by_body


def _collect_shared_curve_adjacency(body_surfaces):
    curve_to_bodies = {}
    for body_idx, surfaces in body_surfaces.items():
        for surface_tag in surfaces:
            try:
                boundary = gmsh.model.getBoundary(
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


def _add_distance_threshold_field(entity_option, entity_tags, lc_min, lc_max, dist_min, dist_max, surfaces=None):
    if not entity_tags:
        return None

    distance_field = gmsh.model.mesh.field.add("Distance")
    gmsh.model.mesh.field.setNumbers(distance_field, entity_option, sorted(entity_tags))
    gmsh.model.mesh.field.setNumber(distance_field, "Sampling", 100)

    threshold_field = gmsh.model.mesh.field.add("Threshold")
    gmsh.model.mesh.field.setNumber(threshold_field, "InField", distance_field)
    gmsh.model.mesh.field.setNumber(threshold_field, "LcMin", lc_min)
    gmsh.model.mesh.field.setNumber(threshold_field, "LcMax", lc_max)
    gmsh.model.mesh.field.setNumber(threshold_field, "DistMin", dist_min)
    gmsh.model.mesh.field.setNumber(threshold_field, "DistMax", max(dist_max, 1e-6))

    if surfaces is None:
        return threshold_field

    restrict_field = gmsh.model.mesh.field.add("Restrict")
    gmsh.model.mesh.field.setNumber(restrict_field, "InField", threshold_field)
    gmsh.model.mesh.field.setNumbers(restrict_field, "SurfacesList", sorted(surfaces))
    return restrict_field


def _add_background_mesh_fields(body_surfaces, body_settings, enable_seam_fields):
    field_ids = []

    for body_idx, surfaces in body_surfaces.items():
        body_min, body_max = body_settings[body_idx]
        body_field = _add_distance_threshold_field(
            "FacesList",
            surfaces,
            body_min * 10,
            body_max * 10,
            0.0,
            body_max * 10,
            surfaces
        )
        if body_field:
            field_ids.append(body_field)

    if enable_seam_fields:
        shared_curves = _collect_shared_curve_adjacency(body_surfaces)
        seam_targets_by_body = {}
        for curve_tag, body_indices in shared_curves.items():
            for body_idx in body_indices:
                if body_idx not in body_surfaces:
                    continue

                body_lc_min = body_settings[body_idx][0] * 10
                finer_adjacent_mins = [
                    body_settings[idx][0] * 10
                    for idx in body_indices
                    if idx != body_idx and body_settings[idx][0] * 10 < body_lc_min
                ]
                if not finer_adjacent_mins:
                    continue

                seam_lc_min = min(finer_adjacent_mins)
                seam_targets_by_body.setdefault(body_idx, {}).setdefault(seam_lc_min, set()).add(curve_tag)

        for body_idx, seam_targets in seam_targets_by_body.items():
            body_lc_max = body_settings[body_idx][1] * 10
            for seam_lc_min, curve_tags in seam_targets.items():
                blend_dist = max(body_lc_max, seam_lc_min) * DEFAULT_SEAM_BLEND_FACTOR

                seam_field = _add_distance_threshold_field(
                    "CurvesList",
                    curve_tags,
                    seam_lc_min,
                    body_lc_max,
                    0.0,
                    blend_dist,
                    body_surfaces[body_idx]
                )
                if seam_field:
                    field_ids.append(seam_field)

    if field_ids:
        if len(field_ids) == 1:
            gmsh.model.mesh.field.setAsBackgroundMesh(field_ids[0])
        else:
            min_field = gmsh.model.mesh.field.add("Min")
            gmsh.model.mesh.field.setNumbers(min_field, "FieldsList", field_ids)
            gmsh.model.mesh.field.setAsBackgroundMesh(min_field)


def _build_gmsh_export_model(step_paths, group_name_map, body_settings, conformal_topology=True):
    gmsh.model.add("FusionExport")

    body_surfaces = {}

    if conformal_topology:
        body_dim_tags = []
        for step_path in step_paths:
            imported_entities = gmsh.model.occ.importShapes(step_path)
            body_dim_tags.append([
                _entity_key(dim_tag)
                for dim_tag in imported_entities
                if dim_tag[0] in (2, 3)
            ])

        gmsh.model.occ.synchronize()
        mapped_body_entities = _fragment_body_entities(body_dim_tags)
        gmsh.model.occ.synchronize()

        for body_idx, group_name in enumerate(group_name_map):
            surfaces = _boundary_surfaces(mapped_body_entities.get(body_idx, []))
            if not surfaces:
                surfaces = _boundary_surfaces(body_dim_tags[body_idx])

            if surfaces:
                body_surfaces[body_idx] = surfaces
                physical_tag = gmsh.model.addPhysicalGroup(2, surfaces)
                gmsh.model.setPhysicalName(2, physical_tag, group_name)
    else:
        for body_idx, (step_path, group_name) in enumerate(zip(step_paths, group_name_map)):
            existing_surfaces = set(tag for _, tag in gmsh.model.getEntities(2))
            gmsh.model.occ.importShapes(step_path)
            gmsh.model.occ.synchronize()
            all_surfaces = set(tag for _, tag in gmsh.model.getEntities(2))
            new_surfaces = sorted(list(all_surfaces - existing_surfaces))

            if new_surfaces:
                body_surfaces[body_idx] = new_surfaces
                physical_tag = gmsh.model.addPhysicalGroup(2, new_surfaces)
                gmsh.model.setPhysicalName(2, physical_tag, group_name)

    _add_background_mesh_fields(
        body_surfaces,
        body_settings,
        enable_seam_fields=conformal_topology
    )


def _write_gmsh_mesh(
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
    gmsh.initialize()
    try:
        _build_gmsh_export_model(
            step_paths,
            group_name_map,
            body_settings,
            conformal_topology=conformal_topology
        )

        gmsh.option.setNumber("Mesh.CharacteristicLengthMin", global_min_val * 10)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMax", global_max_val * 10)
        gmsh.option.setNumber("Mesh.Algorithm", algo_id)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", effective_global_curvature)
        gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
        gmsh.option.setNumber("Mesh.Binary", 0)

        gmsh.model.mesh.generate(2)
        gmsh.write(msh_path)
    finally:
        gmsh.finalize()


def _body_input_id(prefix, idx):
    return f"{prefix}_{idx}"

def run(context):
    global gmsh
    ui = None
    try:
        app = adsk.core.Application.get()
        ui  = app.userInterface

        _cleanup_command_definition(ui)
        handlers.clear()

        if gmsh is None:
            gmsh = _import_gmsh_from_wheelhouse()

        if gmsh is None:
            ui.messageBox(
                "Gmsh module not found. Bundle a compatible gmsh wheel in the 'wheelhouse' "
                "folder next to this script."
            )
            return

        # Create a command definition
        cmd_def = ui.commandDefinitions.addButtonDefinition(COMMAND_ID, 'Export to MSH', 'Generates a .msh file using Gmsh')
        
        # Connect to the command created event
        onCommandCreated = GmshCommandCreatedHandler()
        cmd_def.commandCreated.add(onCommandCreated)
        handlers.append(onCommandCreated)

        # Keep this script alive before Fusion starts command event dispatch.
        adsk.autoTerminate(False)

        # Execute the command
        cmd_def.execute()

    except:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))
        _finish_script(ui)


def stop(context):
    try:
        app = adsk.core.Application.get()
        _finish_script(app.userInterface, terminate=False, cleanup_command=False)
    except Exception:
        handlers.clear()

# --- Event Handlers ---

class GmshCommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        try:
            app = adsk.core.Application.get()
            cmd = args.command
            try:
                cmd.setDialogInitialSize(580, 580)
                cmd.setDialogMinimumSize(360, 360)
            except:
                pass
            inputs = cmd.commandInputs
            design = app.activeProduct
            mesh_settings = _load_mesh_settings()
            default_settings = mesh_settings.get("defaults", {})

            # 2D Algorithm Dropdown
            algo_drop = inputs.addDropDownCommandInput('algo_2d', '2D Algorithm', adsk.core.DropDownStyles.TextListDropDownStyle)
            algo_drop.listItems.add('Automatic', True)
            algo_drop.listItems.add('MeshAdapt', False)
            algo_drop.listItems.add('Delaunay', False)
            algo_drop.listItems.add('Frontal-Delaunay', False)

            inputs.addBoolValueInput(
                'seam_blending',
                'Seam-aware element size blending',
                True,
                '',
                mesh_settings.get("seam_blending", DEFAULT_SEAM_BLENDING_ENABLED)
            )

            visible_bodies = _collect_visible_bodies(design)
            named_bodies = []
            used_group_names = set()
            for body, raw_name in visible_bodies:
                unique_name = _unique_group_name(raw_name, used_group_names)
                named_bodies.append((body, unique_name))

            body_count_input = inputs.addIntegerSpinnerCommandInput('body_count', 'Body Count', 0, 100000, 1, len(named_bodies))
            body_count_input.isVisible = False

            if named_bodies:
                inputs.addTextBoxCommandInput(
                    'body_override_note',
                    'Per-Body Meshing',
                    'Set Min/Max element size and computation of element size from curvature per visible body.',
                    2,
                    True
                )

                table = inputs.addTableCommandInput('body_override_table', 'Body Settings', 4, '3:1:1:1')
                table.maximumVisibleRows = min(
                    len(named_bodies) + 1,
                    BODY_TABLE_MAX_VISIBLE_ROWS
                )
                table.minimumVisibleRows = min(
                    len(named_bodies) + 1,
                    BODY_TABLE_MAX_VISIBLE_ROWS
                )
                table.rowSpacing = 1
                table.columnSpacing = 1
                table.hasGrid = False

                headers = ["Body", "Min (mm)", "Max (mm)", "Curvature"]
                for col_idx, header in enumerate(headers):
                    header_input = inputs.addTextBoxCommandInput(_body_input_id('body_header', col_idx), '', header, 1, True)
                    table.addCommandInput(header_input, 0, col_idx)

                for idx, (_, body_name) in enumerate(named_bodies, start=1):
                    body_saved = mesh_settings.get("by_body", {}).get(body_name, {})
                    body_min, body_max, body_curvature = _sanitize_body_settings(
                        body_saved.get("min", default_settings.get("min", DEFAULT_BODY_MIN)),
                        body_saved.get("max", default_settings.get("max", DEFAULT_BODY_MAX)),
                        body_saved.get("curvature", default_settings.get("curvature", DEFAULT_BODY_CURVATURE))
                    )

                    name_input = inputs.addStringValueInput(_body_input_id('body_name', idx), '', body_name)
                    min_input = inputs.addValueInput(_body_input_id('body_min', idx), '', 'mm', adsk.core.ValueInput.createByReal(body_min))
                    max_input = inputs.addValueInput(_body_input_id('body_max', idx), '', 'mm', adsk.core.ValueInput.createByReal(body_max))
                    curv_input = inputs.addIntegerSpinnerCommandInput(_body_input_id('body_curvature', idx), '', 0, 100, 1, body_curvature)

                    name_input.isReadOnly = True

                    table.addCommandInput(name_input, idx, 0)
                    table.addCommandInput(min_input, idx, 1)
                    table.addCommandInput(max_input, idx, 2)
                    table.addCommandInput(curv_input, idx, 3)

            # Connect to execute event
            onExecute = GmshCommandExecuteHandler()
            cmd.execute.add(onExecute)
            handlers.append(onExecute)

            # Destroy handler to terminate the script properly
            onDestroy = GmshCommandDestroyHandler()
            cmd.destroy.add(onDestroy)
            handlers.append(onDestroy)
            
        except:
            adsk.core.Application.get().userInterface.messageBox('Error creating dialog:\n{}'.format(traceback.format_exc()))
            _finish_script()

class GmshCommandExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        try:
            app = adsk.core.Application.get()
            ui = app.userInterface
            design = app.activeProduct
            inputs = args.command.commandInputs

            # 1. Collect Input Values
            algo_text = inputs.itemById('algo_2d').selectedItem.name
            body_count = inputs.itemById('body_count').value
            seam_blending_input = inputs.itemById('seam_blending')
            seam_blending_enabled = (
                bool(seam_blending_input.value)
                if seam_blending_input
                else DEFAULT_SEAM_BLENDING_ENABLED
            )

            # Map Algorithm Text to Gmsh IDs
            # 1: MeshAdapt, 2: Automatic, 5: Delaunay, 6: Frontal-Delaunay
            algo_map = {"Automatic": 2, "MeshAdapt": 1, "Delaunay": 5, "Frontal-Delaunay": 6}
            algo_id = algo_map.get(algo_text, 2)

            # 2. File Dialog for Save Location
            file_dialog = ui.createFileDialog()
            file_dialog.title = "Save Mesh File"
            file_dialog.filter = 'Mesh Files (*.msh)'
            if file_dialog.showSave() != adsk.core.DialogResults.DialogOK:
                return
            msh_path = file_dialog.filename

            visible_bodies = _collect_visible_bodies(design)
            if not visible_bodies:
                ui.messageBox("No visible bodies found to export.")
                return

            # 3. Export visible bodies to STEP (Temp), one-by-one
            temp_dir = tempfile.gettempdir()
            step_paths = []
            export_mgr = design.exportManager

            group_name_map = []
            used_group_names = set()
            body_settings = []
            settings_by_body = {}

            global_min_val = None
            global_max_val = None
            effective_global_curvature = 0

            for idx, body_info in enumerate(visible_bodies, start=1):
                body = body_info[0]
                raw_name = body_info[1]
                unique_name = _unique_group_name(raw_name, used_group_names)

                body_min = DEFAULT_BODY_MIN
                body_max = DEFAULT_BODY_MAX
                body_curvature = DEFAULT_BODY_CURVATURE
                if idx <= body_count:
                    min_input = inputs.itemById(_body_input_id('body_min', idx))
                    max_input = inputs.itemById(_body_input_id('body_max', idx))
                    curv_input = inputs.itemById(_body_input_id('body_curvature', idx))

                    if min_input:
                        body_min = min_input.value
                    if max_input:
                        body_max = max_input.value
                    if curv_input:
                        body_curvature = curv_input.value

                body_min, body_max, body_curvature = _sanitize_body_settings(body_min, body_max, body_curvature)

                if global_min_val is None or body_min < global_min_val:
                    global_min_val = body_min
                if global_max_val is None or body_max > global_max_val:
                    global_max_val = body_max
                if body_curvature > effective_global_curvature:
                    effective_global_curvature = body_curvature

                step_path = os.path.join(temp_dir, f'fusion_export_temp_{idx}.step')
                _export_body_to_step(design, export_mgr, body, step_path)
                step_paths.append(step_path)
                group_name_map.append(unique_name)
                body_settings.append((body_min, body_max))
                settings_by_body[unique_name] = {
                    "min": body_min,
                    "max": body_max,
                    "curvature": body_curvature,
                }

            try:
                # 4. Gmsh Processing
                used_conformal_topology = seam_blending_enabled
                conformal_error = None
                if seam_blending_enabled:
                    try:
                        _write_gmsh_mesh(
                            msh_path,
                            step_paths,
                            group_name_map,
                            body_settings,
                            global_min_val,
                            global_max_val,
                            effective_global_curvature,
                            algo_id,
                            conformal_topology=True
                        )
                    except Exception as mesh_error:
                        conformal_error = mesh_error
                        used_conformal_topology = False
                        _write_gmsh_mesh(
                            msh_path,
                            step_paths,
                            group_name_map,
                            body_settings,
                            global_min_val,
                            global_max_val,
                            effective_global_curvature,
                            algo_id,
                            conformal_topology=False
                        )
                else:
                    _write_gmsh_mesh(
                        msh_path,
                        step_paths,
                        group_name_map,
                        body_settings,
                        global_min_val,
                        global_max_val,
                        effective_global_curvature,
                        algo_id,
                        conformal_topology=False
                    )

                defaults_min, defaults_max, defaults_curvature = _sanitize_body_settings(
                    global_min_val,
                    global_max_val,
                    effective_global_curvature
                )
                _save_mesh_settings({
                    "defaults": {
                        "min": defaults_min,
                        "max": defaults_max,
                        "curvature": defaults_curvature,
                    },
                    "seam_blending": seam_blending_enabled,
                    "by_body": settings_by_body,
                })
            finally:
                for step_path in step_paths:
                    if os.path.exists(step_path):
                        os.remove(step_path)

            if seam_blending_enabled and used_conformal_topology:
                ui.messageBox(f"Export Complete!\nSaved to: {msh_path}")
            elif seam_blending_enabled:
                ui.messageBox(
                    "Export Complete using fallback meshing.\n"
                    "The conformal shared-topology pass failed, so this mesh may not be watertight at body interfaces.\n\n"
                    f"Original gmsh error:\n{conformal_error}\n\n"
                    f"Saved to: {msh_path}"
                )
            else:
                ui.messageBox(
                    "Export Complete with seam-aware element size blending disabled.\n"
                    f"Saved to: {msh_path}"
                )
            
        except:
            adsk.core.Application.get().userInterface.messageBox('Execution failed:\n{}'.format(traceback.format_exc()))

class GmshCommandDestroyHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        # Let Fusion finish command teardown before stop() releases handlers and
        # deletes the transient command definition.
        try:
            adsk.terminate()
        except Exception:
            pass
