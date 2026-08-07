# Fusion to MSH Export (Fusion Add-In)

<p align="center">
  <img src="scripticon.svg" alt="MSHExport Script Icon" width="25%" />
</p>

Autodesk Fusion script for exporting **visible bodies** to configurable **Gmsh `.msh` meshes** for downstream BEM/FEM workflows.

The script adds an **Export to MSH** command in Fusion, exports each selected body as temporary STEP geometry, and generates a 2D mesh in Gmsh with per-body element sizing controls and optional seam-aware blending for connected bodies.

## Features

- Exports visible **solid and non-solid** bodies from the active design.
- Creates one `.msh` output with physical groups named per body/occurrence.
- Supports per-body mesh controls:
  - Element size
  - Curvature-based sizing weight
- Supports **seam-aware element size blending** for connected bodies:
  - Attempts to create conformal shared topology between touching bodies.
  - Blends only coarser bodies near shared edges toward the smaller adjacent body size, preserving finer bodies' requested sizing.
  - Falls back to the legacy non-conformal export path if Gmsh cannot mesh the shared topology.
- Supports Gmsh 2D algorithms:
  - Automatic
  - MeshAdapt
  - Delaunay
  - Frontal-Delaunay
- Writes ASCII mesh output in Gmsh v2.2 (`.msh`) format for broad compatibility.
- Persists defaults, per-body settings, and the seam-aware blending preference between runs.
- Adds a separate **Export FEM MSH** command for single-body volumetric acoustic FEM meshes:
  - Selects one solid/watertight target body.
  - Exports Gmsh MSH 4.1 tetrahedral volume meshes.
  - Creates a physical volume group for the selected body and physical surface groups for tagged faces.
  - Supports optional face-based boundary groups with local element sizing that blends back to the default volume size.
  - Supports any number of surface groups with live validation and an in-dialog output-file picker.
  - Recalls saved surface names, sizes, face selections, algorithm, and output path for each previously exported body.
  - Includes **Quick Export FEM MSH** for overwriting the most recently exported FEM mesh with its saved settings.

## Requirements

- Autodesk Fusion

For VS Code debugging, breakpoints, Fusion event logging, and unit-test commands, see [DEVELOPMENT.md](DEVELOPMENT.md).

### Gmsh dependency resolution

The script loads the Gmsh library from an included bundled wheel package.

## Repository layout

- `MSHExport.py` — main Fusion add-in
- `MSHExport.manifest` — Fusion add-in manifest
- `Resources/` — separate BEM/FEM export and quick-export toolbar icons, plus transparent icon masters
- `wheelhouse/` — optional bundled wheels (includes `gmsh-4.15.0-...whl`)

At runtime, the script may create:

- `.gmsh_wheels/` — extracted bundled wheel contents
- `.msh_export_settings.json` — saved mesh defaults, per-body overrides, algorithm, and last export path

## Install in Fusion

1. Clone or download this repo and extract its contents into a folder.
2. Open Fusion.
3. Go to **Utilities → Add-Ins**.
4. Click the **+** icon and **Script or add-in from device**
5. Select fusiontomsh folder. Enable the add-in in the list and if needed turn on **Run on Startup**
6. Run the tool using the icons in the Utilities navbar.

## Usage

![Fusion Usage Screenshot](FusionScreenshot.png)

1. In your design, make target bodies (solid or non-solid) **visible**.
2. Use **Utilities → Export to MSH**. The **Export to MSH** dialog opens.
3. Choose a 2D meshing algorithm.
4. Leave **Seam-aware element size blending** enabled for connected-body BEM workflows, or disable it to use the legacy per-body meshing behavior.
5. Adjust per-body `Size` and `Curvature` values.
6. Choose save location for the `.msh` file.
7. The add-in exports temporary STEP geometry, meshes in Gmsh, and saves the result.

After a normal export has ran, use **Quick Export to MSH**
from the same **MSH Export** toolbar panel to overwrite the last `.msh` file using the same settings.

For volumetric acoustic FEM export, use **Export FEM MSH** from the same toolbar panel:

1. Select one solid/watertight target body.
2. Set the default volume mesh size.
3. Use **Add Group** for each optional surface group, then enter its name, local size, and faces. Use **Remove Group** to delete an unneeded group.
4. Use **Browse** in the **Output File** row to choose the `.msh` file. The dialog keeps **Export Mesh** disabled until all entries are valid.
5. The add-in exports a Gmsh MSH 4.1 volume mesh and reports the completion time, physical-group count, and total element count.

After a successful FEM export, **Quick Export FEM MSH** overwrites that file using the saved body, surface groups, face selections, sizes, and algorithm. If model edits invalidate a saved body or face, run the full FEM export again to review the selections.

## Mesh control notes

- Fusion internal length units are converted before meshing.
- Curvature is clamped to `0..100`.
- Only visible bodies are included.
- Seam-aware blending is enabled by default. When enabled, the script first asks Gmsh to fragment touching bodies into shared topology, then applies extra mesh-size fields near shared curves only on bodies that are coarser than an adjacent body.
- The smallest per-body `Size` value is applied as a global Gmsh mesh-size floor, and the largest per-body `Size` value is applied as the global mesh-size ceiling.
- If the conformal topology pass fails during meshing, the script retries with seam-aware blending disabled and warns that the fallback mesh may not be watertight at body interfaces.
- FEM export is intentionally single-body in this first pass. Face boundary tags are matched to imported Gmsh surfaces by geometry, so model edits or STEP topology changes may require retagging.

## Troubleshooting

- **"Gmsh module not found"**
  - Place a compatible `gmsh-*.whl` in `wheelhouse/`, or install `gmsh` in Fusion’s Python environment.
- **No bodies exported**
  - Ensure target bodies are currently **visible**.
- **Unexpected mesh density**
  - Check per-body `Size/Curvature` values and rerun; settings persist in `.msh_export_settings.json`.
- **Fallback meshing warning**
  - Gmsh rejected the seam-aware shared topology for the current geometry. The exported mesh was still written using the legacy path, but connected-body interfaces may need inspection before BEM use.

## License

This project is licensed under the Creative Commons Attribution-NonCommercial 4.0 International license.

See [LICENSE](LICENSE) for details.
