# Fusion to MSH Export (Fusion Add-In)

<p align="center">
  <img src="scripticon.png" alt="MSHExport Script Icon" width="25%" />
</p>

Autodesk Fusion script for exporting **visible bodies** to configurable **Gmsh `.msh` meshes** for downstream BEM/FEM workflows.

The script adds an **Export to MSH** command in Fusion, exports each selected body as temporary STEP geometry, and generates a 2D mesh in Gmsh with per-body sizing controls and optional seam-aware blending for connected bodies.

## Features

- Exports visible **solid and non-solid** bodies from the active design.
- Creates one `.msh` output with physical groups named per body/occurrence.
- Supports per-body mesh controls:
  - Minimum element size
  - Maximum element size
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

## Requirements

- Autodesk Fusion (script runtime)
- Python environment embedded in Fusion

### Gmsh dependency resolution

The script loads the Gmsh library from an included bundled wheel package.

## Repository layout

- `MSHExport.py` — main Fusion add-in
- `MSHExport.manifest` — Fusion add-in manifest
- `wheelhouse/` — optional bundled wheels (includes `gmsh-4.15.0-...whl`)

At runtime, the script may create:

- `.gmsh_wheels/` — extracted bundled wheel contents
- `.msh_export_settings.json` — saved mesh defaults, per-body overrides, algorithm, and last export path

## Install in Fusion

1. Open Fusion.
2. Go to **Utilities → Scripts and Add-Ins**.
3. Open the **Add-Ins** tab.
4. Add this folder as an add-in location if needed.
5. Select `MSHExport` and run it.

## Usage

![Fusion Usage Screenshot](FusionScreenshot.png)

1. In your design, make target bodies (solid or non-solid) **visible**.
2. Use **Utilities → MSH Export → Export to MSH**. The **Export to MSH** dialog opens.
3. Choose a 2D meshing algorithm.
4. Leave **Seam-aware element size blending** enabled for connected-body BEM workflows, or disable it to use the legacy per-body meshing behavior.
5. Adjust per-body `Min`, `Max`, and `Curvature` values.
6. Choose save location for the `.msh` file.
7. The add-in exports temporary STEP geometry, meshes in Gmsh, and saves the result.

After a normal export has saved settings and a path, use **Quick Export to MSH**
from the same **MSH Export** toolbar panel to overwrite the last `.msh` file without opening
the options dialog.

## Mesh control notes

- Fusion internal length units are converted before meshing.
- Curvature is clamped to `0..100`.
- If `Max < Min`, `Max` is adjusted to `Min`.
- Only visible bodies are included.
- Seam-aware blending is enabled by default. When enabled, the script first asks Gmsh to fragment touching bodies into shared topology, then applies extra mesh-size fields near shared curves only on bodies that are coarser than an adjacent body.
- The smallest per-body `Min` value is also applied as a global Gmsh mesh-size floor, so seam blending, curvature sizing, and imported boundary sizing should not request elements below the user-specified minimum.
- If the conformal topology pass fails during meshing, the script retries with seam-aware blending disabled and warns that the fallback mesh may not be watertight at body interfaces.

## Troubleshooting

- **"Gmsh module not found"**
  - Place a compatible `gmsh-*.whl` in `wheelhouse/`, or install `gmsh` in Fusion’s Python environment.
- **No bodies exported**
  - Ensure target bodies are currently **visible**.
- **Unexpected mesh density**
  - Check per-body `Min/Max/Curvature` values and rerun; settings persist in `.msh_export_settings.json`.
- **Fallback meshing warning**
  - Gmsh rejected the seam-aware shared topology for the current geometry. The exported mesh was still written using the legacy path, but connected-body interfaces may need inspection before BEM use.

## License

This project is licensed under the Creative Commons Attribution-NonCommercial 4.0 International license.

See [LICENSE](LICENSE) for details.
