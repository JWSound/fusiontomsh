"""
Fusion 360 script for exporting visible surface bodies to configurable Gmsh
.msh meshes for downstream BEM/FEM analysis.

This module creates a Fusion command that:
- Detects visible non-solid (surface) bodies in the active design.
- Exports each selected body to temporary STEP geometry.
- Builds and meshes geometry with Gmsh using a user-selected 2D algorithm.
- Supports per-body meshing controls (minimum size, maximum size, and
    curvature-based sizing weight) to tune mesh density by region.
- Writes mesh output as .msh files suitable for third-party simulation tools.

Dependency loading strategy:
- First tries a normal installed gmsh package.
- Then tries bundled gmsh wheel files in a local wheelhouse directory.
- Finally falls back to pip installation when available.

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


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WHEELHOUSE_DIR = os.path.join(SCRIPT_DIR, "wheelhouse")
WHEEL_EXTRACT_DIR = os.path.join(SCRIPT_DIR, ".gmsh_wheels")
SETTINGS_PATH = os.path.join(SCRIPT_DIR, ".msh_export_settings.json")

DEFAULT_BODY_MIN = 1.5
DEFAULT_BODY_MAX = 3.0
DEFAULT_BODY_CURVATURE = 0


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


def _import_gmsh_from_wheelhouse():
    if not os.path.isdir(WHEELHOUSE_DIR):
        return None

    wheel_paths = sorted(
        glob.glob(os.path.join(WHEELHOUSE_DIR, "gmsh-*.whl")),
        reverse=True
    )
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


def _import_or_install_gmsh():
    try:
        import gmsh as installed_gmsh
        return installed_gmsh
    except Exception:
        pass

    vendored_gmsh = _import_gmsh_from_wheelhouse()
    if vendored_gmsh is not None:
        return vendored_gmsh

    try:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "gmsh"])
        import gmsh as pip_gmsh
        return pip_gmsh
    except Exception:
        return None


gmsh = _import_or_install_gmsh()

# Global variables to keep handlers in memory
handlers = []


def _is_visible_surface_body(body):
    try:
        if body.isSolid:
            return False
    except:
        return False

    try:
        return body.isVisible
    except:
        try:
            return body.isLightBulbOn
        except:
            return False


def _collect_visible_surface_bodies(design):
    root_comp = design.rootComponent
    visible_bodies = []

    for body in root_comp.bRepBodies:
        if _is_visible_surface_body(body):
            visible_bodies.append((body, body.name or "SurfaceBody"))

    for occurrence in root_comp.allOccurrences:
        occurrence_name = occurrence.fullPathName if occurrence.fullPathName else occurrence.name
        for body in occurrence.bRepBodies:
            if _is_visible_surface_body(body):
                body_name = body.name if body.name else "SurfaceBody"
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


def _body_input_id(prefix, idx):
    return f"{prefix}_{idx}"

def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui  = app.userInterface

        if gmsh is None:
            ui.messageBox(
                "Gmsh module not found. Bundle a gmsh wheel in 'wheelhouse' next to this script "
                "or install with 'pip install gmsh'."
            )
            return

        # Create a command definition
        cmd_def = ui.commandDefinitions.itemById('GmshExportCommand')
        if cmd_def:
            cmd_def.deleteMe()
        
        cmd_def = ui.commandDefinitions.addButtonDefinition('GmshExportCommand', 'Export to MSH', 'Generates a .msh file using Gmsh')
        
        # Connect to the command created event
        onCommandCreated = GmshCommandCreatedHandler()
        cmd_def.commandCreated.add(onCommandCreated)
        handlers.append(onCommandCreated)
        
        # Execute the command
        cmd_def.execute()

        # Prevent the script from terminating until the dialog is closed
        adsk.autoTerminate(False)

    except:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))

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

            visible_surface_bodies = _collect_visible_surface_bodies(design)
            named_bodies = []
            used_group_names = set()
            for body, raw_name in visible_surface_bodies:
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
                table.hasGrid = True

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

                    name_input.isEnabled = False

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

            visible_surface_bodies = _collect_visible_surface_bodies(design)
            if not visible_surface_bodies:
                ui.messageBox("No visible surface bodies found to export.")
                return

            # 3. Export visible surface bodies to STEP (Temp), one-by-one
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

            for idx, body_info in enumerate(visible_surface_bodies, start=1):
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

            # 4. Gmsh Processing
            gmsh.initialize()
            try:
                gmsh.model.add("FusionExport")

                field_ids = []

                for step_path, group_name, (body_min, body_max) in zip(step_paths, group_name_map, body_settings):
                    existing_surfaces = set(tag for _, tag in gmsh.model.getEntities(2))
                    gmsh.model.occ.importShapes(step_path)
                    gmsh.model.occ.synchronize()
                    all_surfaces = set(tag for _, tag in gmsh.model.getEntities(2))
                    new_surfaces = sorted(list(all_surfaces - existing_surfaces))

                    if new_surfaces:
                        physical_tag = gmsh.model.addPhysicalGroup(2, new_surfaces)
                        gmsh.model.setPhysicalName(2, physical_tag, group_name)

                        distance_field = gmsh.model.mesh.field.add("Distance")
                        gmsh.model.mesh.field.setNumbers(distance_field, "FacesList", new_surfaces)

                        threshold_field = gmsh.model.mesh.field.add("Threshold")
                        gmsh.model.mesh.field.setNumber(threshold_field, "InField", distance_field)
                        gmsh.model.mesh.field.setNumber(threshold_field, "LcMin", body_min * 10)
                        gmsh.model.mesh.field.setNumber(threshold_field, "LcMax", body_max * 10)
                        gmsh.model.mesh.field.setNumber(threshold_field, "DistMin", 0.0)
                        gmsh.model.mesh.field.setNumber(threshold_field, "DistMax", max(body_max * 10, 1e-6))

                        restrict_field = gmsh.model.mesh.field.add("Restrict")
                        gmsh.model.mesh.field.setNumber(restrict_field, "InField", threshold_field)
                        gmsh.model.mesh.field.setNumbers(restrict_field, "SurfacesList", new_surfaces)
                        field_ids.append(restrict_field)

                if field_ids:
                    if len(field_ids) == 1:
                        gmsh.model.mesh.field.setAsBackgroundMesh(field_ids[0])
                    else:
                        min_field = gmsh.model.mesh.field.add("Min")
                        gmsh.model.mesh.field.setNumbers(min_field, "FieldsList", field_ids)
                        gmsh.model.mesh.field.setAsBackgroundMesh(min_field)

                # Set Mesh Options
                gmsh.option.setNumber("Mesh.CharacteristicLengthMin", global_min_val * 10) # Convert cm to mm (Fusion internal is cm)
                gmsh.option.setNumber("Mesh.CharacteristicLengthMax", global_max_val * 10)
                gmsh.option.setNumber("Mesh.Algorithm", algo_id)
                gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", effective_global_curvature)

                # ASCII 2.0 Settings (Version 2.2 in Gmsh)
                gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
                gmsh.option.setNumber("Mesh.Binary", 0)

                gmsh.model.mesh.generate(2)
                gmsh.write(msh_path)
            finally:
                gmsh.finalize()

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
                "by_body": settings_by_body,
            })

            for step_path in step_paths:
                if os.path.exists(step_path):
                    os.remove(step_path)

            ui.messageBox(f"Export Complete!\nSaved to: {msh_path}")
            
        except:
            adsk.core.Application.get().userInterface.messageBox('Execution failed:\n{}'.format(traceback.format_exc()))

class GmshCommandDestroyHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        # Allows the script to finish and the "Running" status to disappear
        adsk.terminate()