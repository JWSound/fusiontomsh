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
from datetime import datetime
import html
import importlib
import os
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import export_workflow
import export_types
import fem_model
import fusion_export
import gmsh_model
import gmsh_support
import msh_settings
from msh_settings import (
    ALGO_3D_NAMES,
    ALGO_2D_NAMES,
    DEFAULT_ALGO_3D,
    DEFAULT_ALGO_2D,
    DEFAULT_BODY_CURVATURE,
    DEFAULT_BODY_SIZE,
    DEFAULT_FEM_BOUNDARY_SIZE,
    DEFAULT_FEM_SIZE,
    DEFAULT_SEAM_BLENDING_ENABLED,
    load_mesh_settings,
    sanitize_body_size_settings,
)


WHEELHOUSE_DIR = os.path.join(SCRIPT_DIR, "wheelhouse")
WHEEL_EXTRACT_DIR = os.path.join(SCRIPT_DIR, ".gmsh_wheels")
SETTINGS_PATH = os.path.join(SCRIPT_DIR, ".msh_export_settings.json")
EXPORT_ICON_RESOURCE_FOLDER = os.path.join(SCRIPT_DIR, "Resources", "MSHExport")
QUICK_EXPORT_ICON_RESOURCE_FOLDER = os.path.join(SCRIPT_DIR, "Resources", "MSHQuickExport")
FEM_EXPORT_ICON_RESOURCE_FOLDER = os.path.join(SCRIPT_DIR, "Resources", "MSHFEMExport")
QUICK_FEM_EXPORT_ICON_RESOURCE_FOLDER = os.path.join(SCRIPT_DIR, "Resources", "MSHQuickFEMExport")
EXPORT_COMMAND_ID = "GmshExportCommand"
QUICK_EXPORT_COMMAND_ID = "GmshQuickExportCommand"
FEM_EXPORT_COMMAND_ID = "GmshFEMExportCommand"
QUICK_FEM_EXPORT_COMMAND_ID = "GmshQuickFEMExportCommand"
COMMAND_IDS = (EXPORT_COMMAND_ID, QUICK_EXPORT_COMMAND_ID, FEM_EXPORT_COMMAND_ID, QUICK_FEM_EXPORT_COMMAND_ID)
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


def _reload_local_modules():
    global ALGO_3D_NAMES, ALGO_2D_NAMES
    global DEFAULT_ALGO_3D, DEFAULT_ALGO_2D
    global DEFAULT_BODY_CURVATURE, DEFAULT_BODY_SIZE
    global DEFAULT_FEM_BOUNDARY_SIZE, DEFAULT_FEM_SIZE
    global DEFAULT_SEAM_BLENDING_ENABLED
    global load_mesh_settings, sanitize_body_size_settings

    # Fusion reloads this entry point when an add-in is restarted, but Python can
    # retain imported project modules for the life of the Fusion process.
    for module in (
        msh_settings,
        export_types,
        gmsh_support,
        fusion_export,
        gmsh_model,
        fem_model,
        export_workflow,
    ):
        importlib.reload(module)

    ALGO_3D_NAMES = msh_settings.ALGO_3D_NAMES
    ALGO_2D_NAMES = msh_settings.ALGO_2D_NAMES
    DEFAULT_ALGO_3D = msh_settings.DEFAULT_ALGO_3D
    DEFAULT_ALGO_2D = msh_settings.DEFAULT_ALGO_2D
    DEFAULT_BODY_CURVATURE = msh_settings.DEFAULT_BODY_CURVATURE
    DEFAULT_BODY_SIZE = msh_settings.DEFAULT_BODY_SIZE
    DEFAULT_FEM_BOUNDARY_SIZE = msh_settings.DEFAULT_FEM_BOUNDARY_SIZE
    DEFAULT_FEM_SIZE = msh_settings.DEFAULT_FEM_SIZE
    DEFAULT_SEAM_BLENDING_ENABLED = msh_settings.DEFAULT_SEAM_BLENDING_ENABLED
    load_mesh_settings = msh_settings.load_mesh_settings
    sanitize_body_size_settings = msh_settings.sanitize_body_size_settings


def _log_fem_event(message, is_error=False):
    try:
        adsk.core.Application.log(
            f"MSHExport FEM: {message}",
            adsk.core.LogLevels.ErrorLogLevel if is_error else adsk.core.LogLevels.InfoLogLevel,
            adsk.core.LogTypes.FileLogType,
        )
    except Exception:
        pass


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


def _export_fem_body_to_msh(
    design,
    body,
    body_name,
    msh_path,
    default_size,
    algo_3d_text,
    boundary_groups,
    body_preset_key=None,
):
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
        body_preset_key=body_preset_key,
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

    completed_at = result.get("completed_at", "")
    try:
        timestamp = datetime.fromisoformat(completed_at).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        timestamp = completed_at or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    summary = (
        "FEM mesh export complete.\n"
        f"Completed: {timestamp}\n"
        f"Physical groups: {result.get('group_count', 0):,}\n"
        f"Total elements: {result.get('element_count', 0):,}\n"
        f"Saved to: {result.get('msh_path', '')}"
    )
    unmatched_groups = result.get("unmatched_groups", [])
    if unmatched_groups:
        summary += (
            "\n\nWarning: these surface groups could not be matched after STEP import:\n"
            + "\n".join(unmatched_groups)
        )

    ui.messageBox(summary)
    try:
        ui.statusMessage = f"FEM MSH4 export complete: {os.path.basename(result.get('msh_path', '')) or 'mesh'}"
        adsk.doEvents()
    except Exception:
        return


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


def _entity_token(entity):
    try:
        return entity.entityToken or ""
    except Exception:
        return ""


def _same_fusion_entity(first, second):
    if first is None or second is None:
        return False
    try:
        if first == second:
            return True
    except Exception:
        pass
    try:
        first_native = first.nativeObject or first
        second_native = second.nativeObject or second
        return first_native == second_native
    except Exception:
        return False


def _entities_from_token(design, entity_token):
    if not design or not entity_token:
        return []
    try:
        return list(design.findEntityByToken(entity_token) or [])
    except Exception:
        return []


def _find_fem_body_preset(design, body, fem_settings):
    by_body = fem_settings.get("by_body", {}) if isinstance(fem_settings, dict) else {}
    if not isinstance(by_body, dict):
        return None, None

    current_token = _entity_token(body)
    if current_token and current_token in by_body:
        return current_token, by_body[current_token]

    for preset_key, preset in by_body.items():
        if not isinstance(preset, dict):
            continue
        for candidate in _entities_from_token(design, preset.get("body_token", "")):
            if _same_fusion_entity(candidate, body):
                return preset_key, preset
    return None, None


def _resolved_preset_faces(design, body, face_tokens):
    faces = []
    seen = set()
    for face_token in face_tokens:
        for candidate in _entities_from_token(design, face_token):
            try:
                if not _same_fusion_entity(candidate.body, body):
                    continue
            except Exception:
                continue
            candidate_token = _entity_token(candidate)
            try:
                candidate_key = (candidate_token, candidate.tempId)
            except Exception:
                candidate_key = (candidate_token, id(candidate))
            if candidate_key in seen:
                continue
            seen.add(candidate_key)
            faces.append(candidate)
    return faces


class FEMDialogState:
    def __init__(self, fem_settings):
        self.fem_settings = fem_settings
        self.group_ids = []
        self.next_group_id = 1
        self.body_preset_key = None
        self.restoring = False


def _add_fem_group_inputs(inputs, state, name="", size=None, faces=None):
    container = inputs.itemById("fem_surface_groups")
    if not container:
        _log_fem_event("cannot add surface group: root container 'fem_surface_groups' was not found", True)
        return None

    group_id = state.next_group_id
    state.next_group_id += 1
    state.group_ids.append(group_id)
    group = container.children.addGroupCommandInput(
        _fem_tag_input_id("group", group_id),
        name or f"Surface Group {group_id}",
    )
    group.isExpanded = True
    group_inputs = group.children
    group_inputs.addStringValueInput(_fem_tag_input_id("name", group_id), "Name", name)
    group_inputs.addValueInput(
        _fem_tag_input_id("size", group_id),
        "Mesh Size",
        "mm",
        adsk.core.ValueInput.createByReal(size if size is not None else DEFAULT_FEM_BOUNDARY_SIZE),
    )
    face_selection = group_inputs.addSelectionInput(
        _fem_tag_input_id("faces", group_id),
        "Faces",
        "Select one or more faces on the target body.",
    )
    face_selection.addSelectionFilter("Faces")
    face_selection.setSelectionLimits(0, 0)
    for face in faces or []:
        try:
            face_selection.addSelection(face)
        except Exception:
            pass

    _update_fem_group_controls(inputs, state)
    return group


def _update_fem_group_controls(inputs, state):
    remove_button = inputs.itemById("fem_remove_group")
    if remove_button:
        remove_button.isEnabled = bool(state.group_ids)


def _remove_fem_group_inputs(inputs, state, group_id):
    group = inputs.itemById(_fem_tag_input_id("group", group_id))
    if group:
        group.deleteMe()
    state.group_ids = [existing_id for existing_id in state.group_ids if existing_id != group_id]
    _update_fem_group_controls(inputs, state)


def _set_dropdown_selection(dropdown, item_name):
    if not dropdown:
        return
    for idx in range(dropdown.listItems.count):
        item = dropdown.listItems.item(idx)
        item.isSelected = item.name == item_name


def _restore_fem_body_settings(inputs, state, design, body):
    state.restoring = True
    try:
        for group_id in list(state.group_ids):
            _remove_fem_group_inputs(inputs, state, group_id)

        preset_key, preset = _find_fem_body_preset(design, body, state.fem_settings)
        state.body_preset_key = preset_key
        effective_settings = preset or state.fem_settings

        default_size_input = inputs.itemById("fem_default_size")
        if default_size_input:
            default_size_input.value = effective_settings.get("default_size", DEFAULT_FEM_SIZE)
        _set_dropdown_selection(
            inputs.itemById("fem_algo_3d"),
            effective_settings.get("algo_3d", DEFAULT_ALGO_3D),
        )

        output_path = effective_settings.get("msh_path", "") if preset else state.fem_settings.get("last_msh_path", "")
        output_input = inputs.itemById("fem_output_path")
        if output_input:
            output_input.value = output_path

        for saved_group in effective_settings.get("boundary_groups", []) if preset else []:
            faces = _resolved_preset_faces(
                design,
                body,
                saved_group.get("face_tokens", []),
            )
            _add_fem_group_inputs(
                inputs,
                state,
                saved_group.get("name", ""),
                saved_group.get("size", DEFAULT_FEM_BOUNDARY_SIZE),
                faces,
            )
    finally:
        state.restoring = False


def _fem_face_key(face):
    try:
        return (getattr(face.body, "tempId", None), face.tempId)
    except Exception:
        return (_entity_token(face), id(face))


def _fem_validation_errors(inputs, state):
    errors = []
    body = _selected_entity(inputs.itemById("fem_body"))
    if body is None:
        errors.append("Select one solid body.")
    elif not fusion_export.is_solid_body(body):
        errors.append("The target body must be solid and watertight.")

    try:
        if inputs.itemById("fem_default_size").value <= 0:
            errors.append("Default mesh size must be greater than zero.")
    except Exception:
        errors.append("Enter a valid default mesh size.")

    output_input = inputs.itemById("fem_output_path")
    output_path = output_input.value.strip() if output_input else ""
    if not output_path:
        errors.append("Choose an output .msh file.")
    elif os.path.splitext(output_path)[1].lower() != ".msh":
        errors.append("Output file must use the .msh extension.")
    elif not os.path.isdir(os.path.dirname(os.path.abspath(output_path))):
        errors.append("The output folder does not exist.")

    used_names = set()
    used_faces = set()
    for group_id in state.group_ids:
        name_input = inputs.itemById(_fem_tag_input_id("name", group_id))
        size_input = inputs.itemById(_fem_tag_input_id("size", group_id))
        faces_input = inputs.itemById(_fem_tag_input_id("faces", group_id))
        group_name = name_input.value.strip() if name_input else ""
        faces = _selected_entities(faces_input)

        if not group_name:
            errors.append(f"Surface Group {group_id} needs a name.")
        elif group_name.casefold() in used_names:
            errors.append(f"Surface group name '{group_name}' is duplicated.")
        else:
            used_names.add(group_name.casefold())

        try:
            if not size_input or size_input.value <= 0:
                errors.append(f"Surface group '{group_name or group_id}' needs a positive mesh size.")
        except Exception:
            errors.append(f"Surface group '{group_name or group_id}' has an invalid mesh size.")

        if not faces:
            errors.append(f"Surface group '{group_name or group_id}' needs at least one face.")
        for face in faces:
            try:
                if body is not None and not _same_fusion_entity(face.body, body):
                    errors.append(f"Surface group '{group_name or group_id}' contains a face from another body.")
            except Exception:
                errors.append(f"Surface group '{group_name or group_id}' contains an invalid face.")
            face_key = _fem_face_key(face)
            if face_key in used_faces:
                errors.append("A face can belong to only one surface group.")
            used_faces.add(face_key)

    return errors


def _update_fem_validation_message(inputs, state):
    validation_input = inputs.itemById("fem_validation_message")
    if not validation_input:
        return
    errors = _fem_validation_errors(inputs, state)
    if errors:
        validation_input.formattedText = f"<b>Fix before export:</b> {html.escape(errors[0])}"
    else:
        validation_input.formattedText = "<b>Ready to export.</b>"


def _collect_fem_boundary_groups(inputs, state):
    boundary_groups = []
    for group_id in state.group_ids:
        name_input = inputs.itemById(_fem_tag_input_id("name", group_id))
        size_input = inputs.itemById(_fem_tag_input_id("size", group_id))
        faces_input = inputs.itemById(_fem_tag_input_id("faces", group_id))
        faces = _selected_entities(faces_input)
        boundary_groups.append({
            "name": name_input.value.strip(),
            "size": size_input.value,
            "face_descriptors": [fem_model.fusion_face_descriptor(face) for face in faces],
            "face_tokens": [_entity_token(face) for face in faces if _entity_token(face)],
        })
    return boundary_groups


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
        _reload_local_modules()
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
            FEM_EXPORT_ICON_RESOURCE_FOLDER
        )
        _add_toolbar_button(
            ui,
            QUICK_FEM_EXPORT_COMMAND_ID,
            'Quick Export FEM MSH',
            'Overwrites the last volumetric FEM mesh using its saved body and surface-group settings',
            GmshQuickFEMExportCommandCreatedHandler(),
            QUICK_FEM_EXPORT_ICON_RESOURCE_FOLDER
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
            _log_fem_event("creating export dialog")
            cmd = args.command
            cmd.okButtonText = "Export Mesh"
            try:
                cmd.setDialogInitialSize(660, 640)
                cmd.setDialogMinimumSize(420, 420)
            except:
                pass

            inputs = cmd.commandInputs
            mesh_settings = _load_mesh_settings()
            fem_settings = mesh_settings.get("fem", {})
            state = FEMDialogState(fem_settings)

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
                'Surface Groups',
                'Optional named face groups with local mesh sizing. Add only the groups needed for this body.',
                2,
                True
            )

            groups_container = inputs.addGroupCommandInput("fem_surface_groups", "Surface Groups")
            groups_container.isExpanded = True
            add_group_button = groups_container.children.addBoolValueInput(
                "fem_add_group",
                "Add Group",
                False,
                "",
                False,
            )
            add_group_button.isFullWidth = True
            remove_group_button = groups_container.children.addBoolValueInput(
                "fem_remove_group",
                "Remove Last Group",
                False,
                "",
                False,
            )
            remove_group_button.isFullWidth = True
            remove_group_button.isEnabled = False

            output_table = inputs.addTableCommandInput("fem_output_table", "Output File", 2, "4:1")
            output_table.hasGrid = False
            output_table.minimumVisibleRows = 1
            output_table.maximumVisibleRows = 1
            output_path_input = inputs.addStringValueInput(
                "fem_output_path",
                "",
                fem_settings.get("last_msh_path", ""),
            )
            output_path_input.isReadOnly = True
            browse_button = inputs.addBoolValueInput("fem_browse_output", "Browse...", False, "", False)
            output_table.addCommandInput(output_path_input, 0, 0)
            output_table.addCommandInput(browse_button, 0, 1)

            inputs.addTextBoxCommandInput(
                "fem_validation_message",
                "",
                "<b>Fix before export:</b> Select one solid body.",
                2,
                True,
            )

            onInputChanged = GmshFEMInputChangedHandler(state)
            cmd.inputChanged.add(onInputChanged)
            handlers.append(onInputChanged)

            onValidate = GmshFEMValidateInputsHandler(state)
            cmd.validateInputs.add(onValidate)
            handlers.append(onValidate)

            onExecute = GmshFEMCommandExecuteHandler(state)
            cmd.execute.add(onExecute)
            handlers.append(onExecute)

            onDestroy = GmshCommandDestroyHandler()
            cmd.destroy.add(onDestroy)
            handlers.append(onDestroy)
            _log_fem_event("export dialog created; inputChanged and validateInputs handlers attached")
        except:
            _log_fem_event("dialog creation failed\n{}".format(traceback.format_exc()), True)
            adsk.core.Application.get().userInterface.messageBox('Error creating FEM export dialog:\n{}'.format(traceback.format_exc()))


class GmshFEMInputChangedHandler(adsk.core.InputChangedEventHandler):
    def __init__(self, state):
        super().__init__()
        self.state = state

    def notify(self, args):
        if self.state.restoring:
            return
        try:
            event_args = adsk.core.InputChangedEventArgs.cast(args)
            app = adsk.core.Application.get()
            ui = app.userInterface
            design = app.activeProduct
            changed_input = event_args.input
            command = changed_input.parentCommand if changed_input else None
            inputs = command.commandInputs if command else event_args.inputs
            input_id = changed_input.id if changed_input else ""
            _log_fem_event(f"inputChanged fired for '{input_id}'")

            if input_id == "fem_add_group":
                _add_fem_group_inputs(
                    inputs,
                    self.state,
                    "",
                    self.state.fem_settings.get("boundary_size", DEFAULT_FEM_BOUNDARY_SIZE),
                )
                _log_fem_event(f"surface group added; count={len(self.state.group_ids)}")
            elif input_id == "fem_remove_group":
                if self.state.group_ids:
                    group_id = self.state.group_ids[-1]
                    _remove_fem_group_inputs(inputs, self.state, group_id)
                    _log_fem_event(f"surface group {group_id} removed; count={len(self.state.group_ids)}")
            elif input_id == "fem_browse_output":
                file_dialog = ui.createFileDialog()
                file_dialog.title = "Save FEM Mesh File"
                file_dialog.filter = "Mesh Files (*.msh)"
                current_path = inputs.itemById("fem_output_path").value.strip()
                if current_path:
                    file_dialog.initialFilename = current_path
                if file_dialog.showSave() == adsk.core.DialogResults.DialogOK:
                    selected_path = file_dialog.filename
                    if not os.path.splitext(selected_path)[1]:
                        selected_path += ".msh"
                    inputs.itemById("fem_output_path").value = selected_path
            elif input_id == "fem_body":
                body = _selected_entity(inputs.itemById("fem_body"))
                if body is not None:
                    _restore_fem_body_settings(inputs, self.state, design, body)

            _update_fem_validation_message(inputs, self.state)
        except Exception:
            _log_fem_event("inputChanged failed\n{}".format(traceback.format_exc()), True)
            adsk.core.Application.get().userInterface.messageBox(
                "FEM dialog update failed:\n{}".format(traceback.format_exc())
            )


class GmshFEMValidateInputsHandler(adsk.core.ValidateInputsEventHandler):
    def __init__(self, state):
        super().__init__()
        self.state = state

    def notify(self, args):
        try:
            event_args = adsk.core.ValidateInputsEventArgs.cast(args)
            errors = _fem_validation_errors(event_args.inputs, self.state)
            event_args.areInputsValid = not errors
            _update_fem_validation_message(event_args.inputs, self.state)
        except Exception:
            _log_fem_event("validateInputs failed\n{}".format(traceback.format_exc()), True)
            try:
                event_args.areInputsValid = False
            except Exception:
                pass


class GmshFEMCommandExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self, state):
        super().__init__()
        self.state = state

    def notify(self, args):
        try:
            app = adsk.core.Application.get()
            ui = app.userInterface
            design = app.activeProduct
            inputs = args.command.commandInputs

            validation_errors = _fem_validation_errors(inputs, self.state)
            if validation_errors:
                ui.messageBox("FEM mesh settings are not ready:\n" + "\n".join(validation_errors))
                return

            body = _selected_entity(inputs.itemById('fem_body'))
            default_size_input = inputs.itemById('fem_default_size')
            default_size = default_size_input.value if default_size_input else DEFAULT_FEM_SIZE
            algo_text = inputs.itemById('fem_algo_3d').selectedItem.name
            boundary_groups = _collect_fem_boundary_groups(inputs, self.state)
            msh_path = inputs.itemById("fem_output_path").value.strip()

            body_name = body.name if body.name else "FEMVolume"
            _log_fem_event(
                "export starting; body={!r}, default_size={}, groups={}, output={!r}".format(
                    body_name,
                    default_size,
                    len(boundary_groups),
                    msh_path,
                )
            )
            result = _export_fem_body_to_msh(
                design,
                body,
                body_name,
                msh_path,
                default_size,
                algo_text,
                boundary_groups,
                body_preset_key=self.state.body_preset_key,
            )
            timing_text = ", ".join(
                f"{name}={seconds:.3f}s"
                for name, seconds in result.get("timings", {}).items()
            )
            _log_fem_event(f"export timings: {timing_text}")
            _show_fem_export_result(ui, result)
        except:
            adsk.core.Application.get().userInterface.messageBox('FEM export failed:\n{}'.format(traceback.format_exc()))


def _quick_fem_boundary_groups(design, body, preset):
    boundary_groups = []
    missing_groups = []
    for saved_group in preset.get("boundary_groups", []):
        face_tokens = saved_group.get("face_tokens", [])
        if not face_tokens:
            missing_groups.append(saved_group.get("name", "Surface Group"))
            continue
        faces = []
        seen_faces = set()
        missing_face = False
        for face_token in face_tokens:
            resolved_faces = _resolved_preset_faces(design, body, [face_token])
            if not resolved_faces:
                missing_face = True
            for face in resolved_faces:
                face_key = _fem_face_key(face)
                if face_key not in seen_faces:
                    seen_faces.add(face_key)
                    faces.append(face)
        if missing_face or not faces:
            missing_groups.append(saved_group.get("name", "Surface Group"))
            continue
        boundary_groups.append({
            "name": saved_group.get("name", ""),
            "size": saved_group.get("size", preset.get("default_size", DEFAULT_FEM_SIZE)),
            "face_descriptors": [fem_model.fusion_face_descriptor(face) for face in faces],
            "face_tokens": [_entity_token(face) for face in faces if _entity_token(face)],
        })
    return boundary_groups, missing_groups


class GmshQuickFEMExportCommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self):
        super().__init__()

    def notify(self, args):
        try:
            onExecute = GmshQuickFEMExportCommandExecuteHandler()
            args.command.execute.add(onExecute)
            handlers.append(onExecute)

            onDestroy = GmshCommandDestroyHandler()
            args.command.destroy.add(onDestroy)
            handlers.append(onDestroy)
        except Exception:
            adsk.core.Application.get().userInterface.messageBox(
                "Error creating quick FEM export command:\n{}".format(traceback.format_exc())
            )


class GmshQuickFEMExportCommandExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()

    def notify(self, args):
        try:
            app = adsk.core.Application.get()
            ui = app.userInterface
            design = app.activeProduct
            fem_settings = _load_mesh_settings().get("fem", {})
            preset_key = fem_settings.get("last_body_key", "")
            preset = fem_settings.get("by_body", {}).get(preset_key)
            if not preset:
                ui.messageBox("Quick FEM Export needs a previous FEM export. Run Export FEM MSH first.")
                return

            msh_path = preset.get("msh_path", "") or fem_settings.get("last_msh_path", "")
            output_dir = os.path.dirname(os.path.abspath(msh_path)) if msh_path else ""
            if not msh_path:
                ui.messageBox("Quick FEM Export has no saved output file. Run Export FEM MSH first.")
                return
            if not os.path.isdir(output_dir):
                ui.messageBox(f"Quick FEM Export cannot find the saved output folder:\n{output_dir}")
                return

            body = None
            for candidate in _entities_from_token(design, preset.get("body_token", "")):
                if fusion_export.is_solid_body(candidate):
                    body = candidate
                    break
            if body is None:
                ui.messageBox(
                    "Quick FEM Export could not restore the saved solid body. "
                    "Open Export FEM MSH and select the body again."
                )
                return

            boundary_groups, missing_groups = _quick_fem_boundary_groups(design, body, preset)
            if missing_groups:
                ui.messageBox(
                    "Quick FEM Export could not restore all saved faces for these groups:\n"
                    + "\n".join(missing_groups)
                    + "\n\nOpen Export FEM MSH and review the surface selections."
                )
                return

            result = _export_fem_body_to_msh(
                design,
                body,
                preset.get("body_name", "") or body.name or "FEMVolume",
                msh_path,
                preset.get("default_size", DEFAULT_FEM_SIZE),
                preset.get("algo_3d", DEFAULT_ALGO_3D),
                boundary_groups,
                body_preset_key=preset_key,
            )
            timing_text = ", ".join(
                f"{name}={seconds:.3f}s"
                for name, seconds in result.get("timings", {}).items()
            )
            _log_fem_event(f"quick export timings: {timing_text}")
            _show_fem_export_result(ui, result)
        except Exception:
            adsk.core.Application.get().userInterface.messageBox(
                "Quick FEM export failed:\n{}".format(traceback.format_exc())
            )


class GmshCommandDestroyHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        pass
