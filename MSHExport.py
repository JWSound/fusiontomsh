"""
Autodesk Fusion script for exporting visible bodies to configurable Gmsh
.msh meshes for downstream BEM/FEM analysis.

This module creates a Fusion command that:
- Detects visible bodies (solid and non-solid) in the active design.
- Exports each selected body to temporary STEP geometry.
- Builds and meshes geometry with Gmsh using a user-selected 2D algorithm.
- Supports per-body element size and curvature-based sizing weight to tune
    mesh density by region.
- Writes mesh output as .msh files suitable for third-party BEM/FEM simulation tools.

Dependency loading strategy:
- Loads bundled gmsh wheel files in a local wheelhouse directory.

This script is intended to streamline mesh generation directly from Fusion,
reducing manual export/remeshing steps before external electromagnetic,
acoustic, or structural analyses.
"""

import adsk.core, adsk.fusion, adsk.cam, traceback
import os
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import export_workflow
import fem_model
import fusion_export
import gmsh_support
from msh_settings import (
    ALGO_3D_NAMES,
    ALGO_2D_NAMES,
    DEFAULT_ALGO_3D,
    DEFAULT_ALGO_2D,
    DEFAULT_BODY_CURVATURE,
    DEFAULT_BODY_SIZE,
    DEFAULT_FEM_BOUNDARY_SIZE,
    DEFAULT_FEM_SIZE,
    DEFAULT_FEM_TAG_COUNT,
    DEFAULT_SEAM_BLENDING_ENABLED,
    load_mesh_settings,
    sanitize_body_size_settings,
)


WHEELHOUSE_DIR = os.path.join(SCRIPT_DIR, "wheelhouse")
WHEEL_EXTRACT_DIR = os.path.join(SCRIPT_DIR, ".gmsh_wheels")
SETTINGS_PATH = os.path.join(SCRIPT_DIR, ".msh_export_settings.json")
EXPORT_ICON_RESOURCE_FOLDER = os.path.join(SCRIPT_DIR, "Resources", "MSHExport")
QUICK_EXPORT_ICON_RESOURCE_FOLDER = os.path.join(SCRIPT_DIR, "Resources", "MSHQuickExport")
EXPORT_COMMAND_ID = "GmshExportCommand"
QUICK_EXPORT_COMMAND_ID = "GmshQuickExportCommand"
FEM_EXPORT_COMMAND_ID = "GmshFEMExportCommand"
COMMAND_IDS = (EXPORT_COMMAND_ID, QUICK_EXPORT_COMMAND_ID, FEM_EXPORT_COMMAND_ID)
WORKSPACE_ID = "FusionSolidEnvironment"
TOOLBAR_TAB_ID = "ToolsTab"
PANEL_ID = "GmshExportPanel"
LEGACY_PANEL_IDS = ("SolidScriptsAddinsPanel",)

BODY_TABLE_MAX_VISIBLE_ROWS = 8


def _sanitize_body_settings(size_val, curvature):
    return sanitize_body_size_settings(size_val, curvature)


def _load_mesh_settings():
    return load_mesh_settings(SETTINGS_PATH)


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


def _collect_visible_bodies(design):
    return fusion_export.collect_visible_bodies(design)


def _unique_group_name(base_name, existing_names):
    return fusion_export.unique_group_name(base_name, existing_names)


def _export_visible_bodies_to_msh(design, msh_path, algo_text, seam_blending_enabled, body_setting_resolver):
    return export_workflow.export_visible_bodies_to_msh(
        gmsh,
        design,
        msh_path,
        algo_text,
        seam_blending_enabled,
        body_setting_resolver,
        SETTINGS_PATH,
    )


def _export_fem_body_to_msh(design, body, body_name, msh_path, default_size, algo_3d_text, boundary_groups):
    return export_workflow.export_fem_body_to_msh(
        gmsh,
        design,
        body,
        body_name,
        msh_path,
        default_size,
        algo_3d_text,
        boundary_groups,
        SETTINGS_PATH,
    )


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


def _show_fem_export_result(ui, result):
    if not result.get("success"):
        ui.messageBox(result.get("message"))
        return

    file_name = os.path.basename(result.get("msh_path", "")) or "mesh"
    unmatched_groups = result.get("unmatched_groups", [])
    if unmatched_groups:
        ui.messageBox(
            "FEM mesh export complete, but some boundary groups could not be matched after STEP import:\n"
            + "\n".join(unmatched_groups)
            + f"\n\nSaved to: {result.get('msh_path', '')}"
        )
        return

    try:
        ui.statusMessage = f"FEM MSH4 export complete: {file_name}"
        adsk.doEvents()
    except:
        ui.messageBox(f"FEM mesh export complete.\nSaved to: {result.get('msh_path', '')}")


def _body_input_id(prefix, idx):
    return f"{prefix}_{idx}"


def _fem_tag_input_id(prefix, idx):
    return f"fem_{prefix}_{idx}"


def _selected_entity(selection_input, idx=0):
    if not selection_input or selection_input.selectionCount <= idx:
        return None
    try:
        return selection_input.selection(idx).entity
    except Exception:
        return None


def _selected_entities(selection_input):
    entities = []
    if not selection_input:
        return entities
    for idx in range(selection_input.selectionCount):
        entity = _selected_entity(selection_input, idx)
        if entity:
            entities.append(entity)
    return entities


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
        _add_toolbar_button(
            ui,
            FEM_EXPORT_COMMAND_ID,
            'Export FEM MSH',
            'Generates a volumetric MSH4 FEM mesh from one solid body',
            GmshFEMCommandCreatedHandler(),
            EXPORT_ICON_RESOURCE_FOLDER
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
                    'Set element size and computation of element size from curvature per visible body.',
                    2,
                    True
                )

                table = inputs.addTableCommandInput('body_override_table', 'Body Settings', 3, '3:1:1')
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

                headers = ["Body", "Size (mm)", "Curvature"]
                for col_idx, header in enumerate(headers):
                    header_input = inputs.addTextBoxCommandInput(_body_input_id('body_header', col_idx), '', header, 1, True)
                    table.addCommandInput(header_input, 0, col_idx)

                for idx, (_, body_name) in enumerate(named_bodies, start=1):
                    body_saved = mesh_settings.get("by_body", {}).get(body_name, {})
                    body_size, body_curvature = _sanitize_body_settings(
                        body_saved.get("size", default_settings.get("size", DEFAULT_BODY_SIZE)),
                        body_saved.get("curvature", default_settings.get("curvature", DEFAULT_BODY_CURVATURE))
                    )

                    name_input = inputs.addStringValueInput(_body_input_id('body_name', idx), '', body_name)
                    size_input = inputs.addValueInput(_body_input_id('body_size', idx), '', 'mm', adsk.core.ValueInput.createByReal(body_size))
                    curv_input = inputs.addIntegerSpinnerCommandInput(_body_input_id('body_curvature', idx), '', 0, 100, 1, body_curvature)

                    name_input.isReadOnly = True

                    table.addCommandInput(name_input, idx, 0)
                    table.addCommandInput(size_input, idx, 1)
                    table.addCommandInput(curv_input, idx, 2)

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
                body_size = DEFAULT_BODY_SIZE
                body_curvature = DEFAULT_BODY_CURVATURE
                if idx <= body_count:
                    size_input = inputs.itemById(_body_input_id('body_size', idx))
                    curv_input = inputs.itemById(_body_input_id('body_curvature', idx))

                    if size_input:
                        body_size = size_input.value
                    if curv_input:
                        body_curvature = curv_input.value
                return body_size, body_curvature

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
            default_size, default_curvature = _sanitize_body_settings(
                default_settings.get("size", DEFAULT_BODY_SIZE),
                default_settings.get("curvature", DEFAULT_BODY_CURVATURE)
            )

            def body_setting_resolver(_idx, unique_name):
                body_saved = mesh_settings.get("by_body", {}).get(unique_name, {})
                return _sanitize_body_settings(
                    body_saved.get("size", default_size),
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


class GmshFEMCommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        try:
            cmd = args.command
            try:
                cmd.setDialogInitialSize(640, 620)
                cmd.setDialogMinimumSize(420, 420)
            except:
                pass

            inputs = cmd.commandInputs
            mesh_settings = _load_mesh_settings()
            fem_settings = mesh_settings.get("fem", {})

            body_selection = inputs.addSelectionInput(
                'fem_body',
                'Target Body',
                'Select one solid/watertight body to export as a FEM volume.'
            )
            body_selection.addSelectionFilter('SolidBodies')
            body_selection.setSelectionLimits(1, 1)

            default_size = fem_settings.get("default_size", DEFAULT_FEM_SIZE)
            inputs.addValueInput(
                'fem_default_size',
                'Default Size',
                'mm',
                adsk.core.ValueInput.createByReal(default_size)
            )

            algo_drop = inputs.addDropDownCommandInput(
                'fem_algo_3d',
                '3D Algorithm',
                adsk.core.DropDownStyles.TextListDropDownStyle
            )
            saved_algo = fem_settings.get("algo_3d", DEFAULT_ALGO_3D)
            for algo_name in ALGO_3D_NAMES:
                algo_drop.listItems.add(algo_name, algo_name == saved_algo)

            inputs.addTextBoxCommandInput(
                'fem_boundary_note',
                'Boundary Tags',
                'Optional named face groups. Each group can have a smaller element size that blends back to the default size.',
                2,
                True
            )

            boundary_size = fem_settings.get("boundary_size", DEFAULT_FEM_BOUNDARY_SIZE)
            default_names = ("Radiator", "Port", "Boundary 3", "Boundary 4")
            for idx in range(1, DEFAULT_FEM_TAG_COUNT + 1):
                group = inputs.addGroupCommandInput(_fem_tag_input_id('group', idx), f"Boundary Group {idx}")
                group.isExpanded = idx == 1
                group_inputs = group.children

                group_inputs.addStringValueInput(
                    _fem_tag_input_id('name', idx),
                    'Name',
                    default_names[idx - 1] if idx <= len(default_names) else f"Boundary {idx}"
                )
                group_inputs.addValueInput(
                    _fem_tag_input_id('size', idx),
                    'Size',
                    'mm',
                    adsk.core.ValueInput.createByReal(boundary_size)
                )
                face_selection = group_inputs.addSelectionInput(
                    _fem_tag_input_id('faces', idx),
                    'Faces',
                    'Select one or more faces for this boundary group.'
                )
                face_selection.addSelectionFilter('Faces')
                face_selection.setSelectionLimits(0, 0)

            onExecute = GmshFEMCommandExecuteHandler()
            cmd.execute.add(onExecute)
            handlers.append(onExecute)

            onDestroy = GmshCommandDestroyHandler()
            cmd.destroy.add(onDestroy)
            handlers.append(onDestroy)
        except:
            adsk.core.Application.get().userInterface.messageBox('Error creating FEM export dialog:\n{}'.format(traceback.format_exc()))


class GmshFEMCommandExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        try:
            app = adsk.core.Application.get()
            ui = app.userInterface
            design = app.activeProduct
            inputs = args.command.commandInputs

            body = _selected_entity(inputs.itemById('fem_body'))
            if body is None:
                ui.messageBox("Select one solid body before exporting a FEM mesh.")
                return
            if not fusion_export.is_solid_body(body):
                ui.messageBox("The selected target body is not a solid/watertight body.")
                return

            default_size_input = inputs.itemById('fem_default_size')
            default_size = default_size_input.value if default_size_input else DEFAULT_FEM_SIZE
            algo_text = inputs.itemById('fem_algo_3d').selectedItem.name

            boundary_groups = []
            used_faces = set()
            for idx in range(1, DEFAULT_FEM_TAG_COUNT + 1):
                name_input = inputs.itemById(_fem_tag_input_id('name', idx))
                size_input = inputs.itemById(_fem_tag_input_id('size', idx))
                faces_input = inputs.itemById(_fem_tag_input_id('faces', idx))
                group_name = name_input.value.strip() if name_input else ""
                faces = _selected_entities(faces_input)
                if not group_name or not faces:
                    continue

                duplicate_faces = []
                face_descriptors = []
                for face in faces:
                    try:
                        token = getattr(face, "tempId", None) or getattr(face, "entityToken", None) or id(face)
                    except Exception:
                        token = id(face)
                    if token in used_faces:
                        duplicate_faces.append(group_name)
                        continue
                    used_faces.add(token)
                    face_descriptors.append(fem_model.fusion_face_descriptor(face))

                if duplicate_faces:
                    ui.messageBox("A face can only belong to one boundary group. Remove duplicate face selections and try again.")
                    return

                boundary_groups.append({
                    "name": group_name,
                    "size": size_input.value if size_input else default_size,
                    "face_descriptors": face_descriptors,
                })

            file_dialog = ui.createFileDialog()
            file_dialog.title = "Save FEM Mesh File"
            file_dialog.filter = 'Mesh Files (*.msh)'
            saved_path = _load_mesh_settings().get("fem", {}).get("last_msh_path", "")
            if saved_path:
                file_dialog.initialFilename = saved_path
            if file_dialog.showSave() != adsk.core.DialogResults.DialogOK:
                return

            body_name = body.name if body.name else "FEMVolume"
            result = _export_fem_body_to_msh(
                design,
                body,
                body_name,
                file_dialog.filename,
                default_size,
                algo_text,
                boundary_groups,
            )
            _show_fem_export_result(ui, result)
        except:
            adsk.core.Application.get().userInterface.messageBox('FEM export failed:\n{}'.format(traceback.format_exc()))


class GmshCommandDestroyHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        pass
