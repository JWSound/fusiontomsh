# v0.1.0

Initial packaged release of Fusion to MSH Export.

- Export visible Fusion bodies as tagged BEM surface meshes with per-body size
  and curvature controls.
- Export a selected solid as a tetrahedral FEM mesh with named boundary groups.
- Quickly repeat BEM and FEM exports with saved settings.
- Blend element sizes at connected-body seams and repair near-coincident planar
  interface rims within 0.01 mm before fragmentation.
- Bundle Gmsh 4.15.0 in separate Windows x64 and macOS Apple Silicon ZIPs.

## Installation

Download the ZIP for your platform, extract it, then add its `MSHExport` folder
through Fusion's **Utilities > Add-Ins > Scripts and Add-Ins > + > Script or
add-in from device**. Select the add-in and click **Run**. See the README for
the complete installation steps.

## Validation and limits

The Python regression suite and a mesh-generation smoke test pass on Windows
with the bundled Gmsh 4.15.0 runtime. The macOS ARM64 package includes the matching
upstream wheel; native macOS/Fusion testing has not been performed for this release.

Rim repair handles single-face planar caps with unique matching perimeter
curves. It skips larger gaps and unsupported or ambiguous geometry. Inspect
mesh connectivity before solving; source CAD may still need repair. Reduced
meshes require the appropriate symmetry settings in the downstream solver.
