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

from export_types import BodyMeshSettings, ExportBody
import gmsh_model
import gmsh_support
from msh_settings import (
    ALGO_2D_NAMES,
    DEFAULT_ALGO_2D,
    DEFAULT_BODY_CURVATURE,
    DEFAULT_BODY_MAX,
    DEFAULT_BODY_MIN,
    DEFAULT_SEAM_BLENDING_ENABLED,
    algo_text_to_id,
    load_mesh_settings,
    sanitize_body_settings,
    save_mesh_settings,
)


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WHEELHOUSE_DIR = os.path.join(SCRIPT_DIR, "wheelhouse")
WHEEL_EXTRACT_DIR = os.path.join(SCRIPT_DIR, ".gmsh_wheels")
SETTINGS_PATH = os.path.join(SCRIPT_DIR, ".msh_export_settings.json")
EXPORT_ICON_RESOURCE_FOLDER = os.path.join(SCRIPT_DIR, "Resources", "MSHExport")
QUICK_EXPORT_ICON_RESOURCE_FOLDER = os.path.join(SCRIPT_DIR, "Resources", "MSHQuickExport")
EXPORT_COMMAND_ID = "GmshExportCommand"
QUICK_EXPORT_COMMAND_ID = "GmshQuickExportCommand"
COMMAND_IDS = (EXPORT_COMMAND_ID, QUICK_EXPORT_COMMAND_ID)
WORKSPACE_ID = "FusionSolidEnvironment"
TOOLBAR_TAB_ID = "ToolsTab"
PANEL_ID = "GmshExportPanel"
LEGACY_PANEL_IDS = ("SolidScriptsAddinsPanel",)

BODY_TABLE_MAX_VISIBLE_ROWS = 8


def _sanitize_body_settings(min_val, max_val, curvature):
    return sanitize_body_settings(min_val, max_val, curvature)


def _load_mesh_settings():
    return load_mesh_settings(SETTINGS_PATH)


def _save_mesh_settings(settings_data):
    save_mesh_settings(SETTINGS_PATH, settings_data)


def _import_gmsh_from_wheelhouse():
    return gmsh_support.import_gmsh_from_wheelhouse(WHEELHOUSE_DIR, WHEEL_EXTRACT_DIR)


gmsh = None

# Global variables to keep handlers in memory
handlers = []


def _cleanup_command_definition(ui=None):
    try:
        if ui is None:
            ui = adsk.core.Application.get().userInterface

        for panel_id in (PANEL_ID,) + LEGACY_PANEL_IDS:
            panel = ui.allToolbarPanels.itemById(panel_id)
            if not panel:
                continue
            for command_id in COMMAND_IDS:
                control = panel.controls.itemById(command_id)
                if control:
                    control.deleteMe()

        panel = ui.allToolbarPanels.itemById(PANEL_ID)
        if panel:
            panel.deleteMe()

        for command_id in COMMAND_IDS:
            cmd_def = ui.commandDefinitions.itemById(command_id)
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


def _gmsh_length(fusion_length):
    return gmsh_support.gmsh_length(fusion_length)


def _set_gmsh_option_if_available(name, value):
    return gmsh_support.set_gmsh_option_if_available(gmsh, name, value)


def _apply_global_mesh_size_limits(min_size, max_size):
    gmsh_support.apply_global_mesh_size_limits(gmsh, min_size, max_size)


def _install_global_min_mesh_size_callback(min_size):
    gmsh_support.install_global_min_mesh_size_callback(gmsh, min_size)


def _build_gmsh_export_model(step_paths, group_name_map, body_settings, conformal_topology=True):
    gmsh_model.build_gmsh_export_model(
        gmsh,
        step_paths,
        group_name_map,
        body_settings,
        conformal_topology=conformal_topology
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
    def build_model():
        _build_gmsh_export_model(
            step_paths,
            group_name_map,
            body_settings,
            conformal_topology=conformal_topology
        )

    gmsh_support.write_gmsh_mesh(
        gmsh,
        msh_path,
        build_model,
        global_min_val,
        global_max_val,
        effective_global_curvature,
        algo_id,
    )


def _algo_text_to_id(algo_text):
    return algo_text_to_id(algo_text)


def _export_visible_bodies_to_msh(design, msh_path, algo_text, seam_blending_enabled, body_setting_resolver):
    visible_bodies = _collect_visible_bodies(design)
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
        unique_name = _unique_group_name(raw_name, used_group_names)

        body_min, body_max, body_curvature = body_setting_resolver(idx, unique_name)
        body_min, body_max, body_curvature = _sanitize_body_settings(body_min, body_max, body_curvature)

        if global_min_val is None or body_min < global_min_val:
            global_min_val = body_min
        if global_max_val is None or body_max > global_max_val:
            global_max_val = body_max
        if body_curvature > effective_global_curvature:
            effective_global_curvature = body_curvature

        step_path = os.path.join(temp_dir, f'fusion_export_temp_{idx}.step')
        _export_body_to_step(design, export_mgr, body, step_path)
        mesh_settings = BodyMeshSettings(body_min, body_max, body_curvature)
        export_bodies.append(ExportBody(body, unique_name, mesh_settings, step_path))
        settings_by_body[unique_name] = {
            "min": body_min,
            "max": body_max,
            "curvature": body_curvature,
        }

    used_conformal_topology = seam_blending_enabled
    conformal_error = None

    try:
        step_paths = [export_body.step_path for export_body in export_bodies]
        group_name_map = [export_body.group_name for export_body in export_bodies]
        body_settings = [
            (export_body.settings.min_size, export_body.settings.max_size)
            for export_body in export_bodies
        ]

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
                    _algo_text_to_id(algo_text),
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
                    _algo_text_to_id(algo_text),
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
                _algo_text_to_id(algo_text),
                conformal_topology=False
            )

        defaults_min, defaults_max, defaults_curvature = _sanitize_body_settings(
            global_min_val,
            global_max_val,
            effective_global_curvature
        )
        _save_mesh_settings({
            "last_msh_path": msh_path,
            "algo_2d": algo_text,
            "defaults": {
                "min": defaults_min,
                "max": defaults_max,
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


def _format_export_result_message(result):
    msh_path = result.get("msh_path", "")
    if result.get("used_conformal_topology"):
        return f"Export Complete!\nSaved to: {msh_path}"

    if result.get("conformal_error") is not None:
        return (
            "Export Complete using fallback meshing.\n"
            "The conformal shared-topology pass failed, so this mesh may not be watertight at body interfaces.\n\n"
            f"Original gmsh error:\n{result.get('conformal_error')}\n\n"
            f"Saved to: {msh_path}"
        )

    return (
        "Export Complete with seam-aware element size blending disabled.\n"
        f"Saved to: {msh_path}"
    )


def _format_export_status_message(result):
    msh_path = result.get("msh_path", "")
    file_name = os.path.basename(msh_path) if msh_path else "mesh"
    if result.get("used_conformal_topology"):
        return f"MSH export complete: {file_name}"
    return f"MSH export complete with seam blending disabled: {file_name}"


def _show_export_result(ui, result):
    if not result.get("success"):
        ui.messageBox(result.get("message"))
        return

    if result.get("conformal_error") is not None:
        ui.messageBox(_format_export_result_message(result))
        return

    try:
        ui.statusMessage = _format_export_status_message(result)
        adsk.doEvents()
    except:
        ui.messageBox(_format_export_result_message(result))


def _body_input_id(prefix, idx):
    return f"{prefix}_{idx}"


def _get_export_panel(ui):
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    if not workspace:
        return ui.allToolbarPanels.itemById(PANEL_ID)

    tab = workspace.toolbarTabs.itemById(TOOLBAR_TAB_ID)
    if tab:
        panel = tab.toolbarPanels.itemById(PANEL_ID)
        if panel:
            return panel

        try:
            return tab.toolbarPanels.add(PANEL_ID, "MSH Export", "SolidScriptsAddinsPanel", False)
        except:
            return tab.toolbarPanels.add(PANEL_ID, "MSH Export")

    panel = workspace.toolbarPanels.itemById(PANEL_ID)
    if panel:
        return panel

    try:
        return workspace.toolbarPanels.add(PANEL_ID, "MSH Export")
    except:
        pass

    return ui.allToolbarPanels.itemById(PANEL_ID)


def _add_toolbar_button(ui, command_id, name, description, handler, icon_resource_folder):
    cmd_def = ui.commandDefinitions.itemById(command_id)
    if not cmd_def:
        cmd_def = ui.commandDefinitions.addButtonDefinition(command_id, name, description, icon_resource_folder)
    else:
        cmd_def.resourceFolder = icon_resource_folder

    cmd_def.commandCreated.add(handler)
    handlers.append(handler)

    panel = _get_export_panel(ui)
    if not panel:
        raise RuntimeError("Could not create Fusion's Utilities > MSH Export toolbar panel.")

    control = panel.controls.itemById(command_id)
    if not control:
        control = panel.controls.addCommand(cmd_def)
    control.isPromoted = True
    control.isPromotedByDefault = True
    return control


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

        _add_toolbar_button(
            ui,
            EXPORT_COMMAND_ID,
            'Export to MSH',
            'Generates a .msh file using Gmsh',
            GmshCommandCreatedHandler(),
            EXPORT_ICON_RESOURCE_FOLDER
        )
        _add_toolbar_button(
            ui,
            QUICK_EXPORT_COMMAND_ID,
            'Quick Export to MSH',
            'Overwrites the last .msh export using the saved mesh settings',
            GmshQuickExportCommandCreatedHandler(),
            QUICK_EXPORT_ICON_RESOURCE_FOLDER
        )

        adsk.autoTerminate(False)

    except:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))
        _finish_script(ui)


def stop(context):
    try:
        app = adsk.core.Application.get()
        _finish_script(app.userInterface, terminate=False, cleanup_command=True)
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
            saved_algo = mesh_settings.get("algo_2d", DEFAULT_ALGO_2D)
            for algo_name in ALGO_2D_NAMES:
                algo_drop.listItems.add(algo_name, algo_name == saved_algo)

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

            # 2. File Dialog for Save Location
            file_dialog = ui.createFileDialog()
            file_dialog.title = "Save Mesh File"
            file_dialog.filter = 'Mesh Files (*.msh)'
            saved_path = _load_mesh_settings().get("last_msh_path", "")
            if saved_path:
                file_dialog.initialFilename = saved_path
            if file_dialog.showSave() != adsk.core.DialogResults.DialogOK:
                return
            msh_path = file_dialog.filename

            def body_setting_resolver(idx, _unique_name):
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
                return body_min, body_max, body_curvature

            result = _export_visible_bodies_to_msh(
                design,
                msh_path,
                algo_text,
                seam_blending_enabled,
                body_setting_resolver
            )

            _show_export_result(ui, result)
            
        except:
            adsk.core.Application.get().userInterface.messageBox('Execution failed:\n{}'.format(traceback.format_exc()))


class GmshQuickExportCommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        try:
            cmd = args.command

            onExecute = GmshQuickExportCommandExecuteHandler()
            cmd.execute.add(onExecute)
            handlers.append(onExecute)

            onDestroy = GmshCommandDestroyHandler()
            cmd.destroy.add(onDestroy)
            handlers.append(onDestroy)
        except:
            adsk.core.Application.get().userInterface.messageBox('Error creating quick export command:\n{}'.format(traceback.format_exc()))


class GmshQuickExportCommandExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        try:
            app = adsk.core.Application.get()
            ui = app.userInterface
            design = app.activeProduct
            mesh_settings = _load_mesh_settings()

            msh_path = mesh_settings.get("last_msh_path", "")
            if not msh_path:
                ui.messageBox("Quick Export needs a saved mesh path. Run Export to MSH once and choose a save location.")
                return

            output_dir = os.path.dirname(msh_path)
            if output_dir and not os.path.isdir(output_dir):
                ui.messageBox(f"Quick Export cannot find the saved output folder:\n{output_dir}")
                return

            default_settings = mesh_settings.get("defaults", {})
            default_min, default_max, default_curvature = _sanitize_body_settings(
                default_settings.get("min", DEFAULT_BODY_MIN),
                default_settings.get("max", DEFAULT_BODY_MAX),
                default_settings.get("curvature", DEFAULT_BODY_CURVATURE)
            )

            def body_setting_resolver(_idx, unique_name):
                body_saved = mesh_settings.get("by_body", {}).get(unique_name, {})
                return _sanitize_body_settings(
                    body_saved.get("min", default_min),
                    body_saved.get("max", default_max),
                    body_saved.get("curvature", default_curvature)
                )

            result = _export_visible_bodies_to_msh(
                design,
                msh_path,
                mesh_settings.get("algo_2d", DEFAULT_ALGO_2D),
                mesh_settings.get("seam_blending", DEFAULT_SEAM_BLENDING_ENABLED),
                body_setting_resolver
            )

            _show_export_result(ui, result)
        except:
            adsk.core.Application.get().userInterface.messageBox('Quick export failed:\n{}'.format(traceback.format_exc()))


class GmshCommandDestroyHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        pass
