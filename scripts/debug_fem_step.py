"""Run a Fusion-exported STEP file through the FEM meshing pipeline.

This script deliberately stops outside the Fusion API boundary: it feeds an
existing STEP file to the same fem_model and gmsh_support helpers used by the
add-in.  On a 3D meshing failure it keeps the successfully generated boundary
mesh and writes entity details for any surface tags mentioned by Gmsh.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

import fem_model  # noqa: E402
import gmsh_support  # noqa: E402


ALGORITHM_IDS = {
    "automatic": None,
    "delaunay": 1,
    "frontal": 4,
    "hxt": 10,
}
SURFACE_ALGORITHM_IDS = {
    "automatic": None,
    "meshadapt": 1,
    "delaunay": 5,
    "frontal": 6,
}
SURFACE_TAG_PATTERN = re.compile(r"surface\s+(\d+)", re.IGNORECASE)
OVERLAPPING_FACET_PATTERN = re.compile(
    r"(?:1st|2nd):\s*\[([^]]+)\]\s*#(\d+)", re.IGNORECASE
)


def _arguments():
    parser = argparse.ArgumentParser(
        description="Mesh an existing STEP file with Fusion2msh's FEM pipeline."
    )
    parser.add_argument("step_path", type=Path)
    parser.add_argument(
        "--size-mm",
        type=float,
        default=12.0,
        help="Uniform target element size in millimetres (default: 12).",
    )
    parser.add_argument(
        "--algorithm",
        choices=tuple(ALGORITHM_IDS),
        default="automatic",
        help="Gmsh 3D algorithm (default: automatic, matching the add-in).",
    )
    parser.add_argument(
        "--surface-algorithm",
        choices=tuple(SURFACE_ALGORITHM_IDS),
        default="automatic",
        help="Gmsh 2D algorithm (default: automatic, matching the add-in).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Diagnostic output directory (default: runs/gmsh_step_debug/<stem>).",
    )
    parser.add_argument(
        "--refine-surface",
        action="append",
        default=[],
        metavar="TAG:SIZE_MM",
        help="Locally refine a Gmsh surface tag; may be repeated.",
    )
    return parser.parse_args()


def _surface_refinements(values):
    refinements = []
    for value in values:
        try:
            tag_text, size_text = value.split(":", 1)
            tag = int(tag_text)
            size_mm = float(size_text)
        except ValueError as exc:
            raise SystemExit(
                f"Invalid --refine-surface value {value!r}; expected TAG:SIZE_MM"
            ) from exc
        if tag <= 0 or size_mm <= 0:
            raise SystemExit("Surface tags and refinement sizes must be greater than zero")
        refinements.append({"tag": tag, "size_mm": size_mm})
    return refinements


def _surface_details(gmsh, surface_tag):
    upward, downward = gmsh.model.getAdjacencies(2, surface_tag)
    element_types, element_tags, _ = gmsh.model.mesh.getElements(2, surface_tag)
    node_tags, _, _ = gmsh.model.mesh.getNodes(2, surface_tag, includeBoundary=True)
    return {
        "tag": surface_tag,
        "type": gmsh.model.getType(2, surface_tag),
        "area_mm2": gmsh.model.occ.getMass(2, surface_tag),
        "center_of_mass_mm": list(gmsh.model.occ.getCenterOfMass(2, surface_tag)),
        "bounding_box_mm": list(gmsh.model.getBoundingBox(2, surface_tag)),
        "adjacent_volumes": [int(tag) for tag in upward],
        "boundary_curves": [int(tag) for tag in downward],
        "node_count_including_boundary": len(node_tags),
        "element_counts": {
            str(element_type): len(tags)
            for element_type, tags in zip(element_types, element_tags)
        },
    }


def _curve_details(gmsh, curve_tag):
    upward, downward = gmsh.model.getAdjacencies(1, curve_tag)
    node_tags, _, _ = gmsh.model.mesh.getNodes(1, curve_tag, includeBoundary=True)
    return {
        "tag": curve_tag,
        "type": gmsh.model.getType(1, curve_tag),
        "length_mm": gmsh.model.occ.getMass(1, curve_tag),
        "center_of_mass_mm": list(gmsh.model.occ.getCenterOfMass(1, curve_tag)),
        "bounding_box_mm": list(gmsh.model.getBoundingBox(1, curve_tag)),
        "adjacent_surfaces": [int(tag) for tag in upward],
        "boundary_points": [int(tag) for tag in downward],
        "mesh_nodes": [_node_details(gmsh, int(tag)) for tag in node_tags],
    }


def _overlapping_facets(gmsh, log_lines):
    facets = []
    for line in log_lines:
        match = OVERLAPPING_FACET_PATTERN.search(line)
        if not match:
            continue
        node_tags = [int(value.strip()) for value in match.group(1).split(",")]
        facets.append(
            {
                "surface_tag": int(match.group(2)),
                "node_tags": node_tags,
                "nodes": [_node_details(gmsh, node_tag) for node_tag in node_tags],
            }
        )
    return facets


def _node_details(gmsh, node_tag):
    coordinates, parametric_coordinates, entity_dim, entity_tag = (
        gmsh.model.mesh.getNode(node_tag)
    )
    return {
        "tag": node_tag,
        "coordinates_mm": list(coordinates),
        "classified_on": [int(entity_dim), int(entity_tag)],
        "parametric_coordinates": list(parametric_coordinates),
    }


def _volume_mesh_summary(gmsh):
    element_types, element_tags, _ = gmsh.model.mesh.getElements(3)
    counts = {
        str(element_type): len(tags)
        for element_type, tags in zip(element_types, element_tags)
    }
    all_element_tags = [int(tag) for tags in element_tags for tag in tags]
    if not all_element_tags:
        return {"element_counts": counts}

    qualities = gmsh.model.mesh.getElementQualities(all_element_tags, "minSICN")
    quality_values = [float(value) for value in qualities]
    return {
        "element_counts": counts,
        "quality_metric": "minSICN",
        "minimum_quality": min(quality_values),
        "maximum_quality": max(quality_values),
        "mean_quality": sum(quality_values) / len(quality_values),
        "non_positive_quality_count": sum(value <= 0 for value in quality_values),
    }


def _write_json(path, value):
    with path.open("w", encoding="utf-8") as output_file:
        json.dump(value, output_file, indent=2)
        output_file.write("\n")


def main():
    args = _arguments()
    step_path = args.step_path.resolve()
    if not step_path.is_file():
        raise SystemExit(f"STEP file not found: {step_path}")
    if args.size_mm <= 0:
        raise SystemExit("--size-mm must be greater than zero")
    refinements = _surface_refinements(args.refine_surface)

    output_dir = (
        args.output_dir.resolve()
        if args.output_dir
        else REPOSITORY_ROOT / "runs" / "gmsh_step_debug" / step_path.stem
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    gmsh = gmsh_support.import_gmsh_from_wheelhouse(
        str(REPOSITORY_ROOT / "wheelhouse"),
        str(REPOSITORY_ROOT / ".gmsh_wheels"),
    )
    if gmsh is None:
        raise SystemExit("Could not load the bundled Gmsh wheel")

    gmsh.initialize()
    gmsh.logger.start()
    gmsh.option.setNumber("General.Terminal", 0)
    mesh_error = None
    model_info = {}
    surface_tags_from_error = []
    volume_mesh = {}
    try:
        # fem_model expects Fusion's internal centimetres and converts them to
        # Gmsh millimetres, just as export_fem_body_to_msh does in the add-in.
        fusion_size_cm = args.size_mm / gmsh_support.FUSION_TO_GMSH_LENGTH_SCALE
        model_info = fem_model.build_fem_export_model(
            gmsh,
            str(step_path),
            step_path.stem,
            fusion_size_cm,
            [],
        )

        available_surface_tags = {tag for _, tag in gmsh.model.getEntities(2)}
        missing_surface_tags = sorted(
            refinement["tag"]
            for refinement in refinements
            if refinement["tag"] not in available_surface_tags
        )
        if missing_surface_tags:
            raise RuntimeError(
                "Requested refinement surfaces do not exist: "
                + ", ".join(str(tag) for tag in missing_surface_tags)
            )
        if refinements:
            fem_model._add_surface_size_fields(
                gmsh,
                [
                    {
                        "surface_tags": [refinement["tag"]],
                        "size": refinement["size_mm"]
                        / gmsh_support.FUSION_TO_GMSH_LENGTH_SCALE,
                    }
                    for refinement in refinements
                ],
                fusion_size_cm,
            )

        minimum_size_mm = min(
            [args.size_mm, *(refinement["size_mm"] for refinement in refinements)]
        )
        gmsh_support.apply_global_mesh_size_limits(
            gmsh, minimum_size_mm, args.size_mm
        )
        gmsh_support.install_global_min_mesh_size_callback(gmsh, minimum_size_mm)
        gmsh_support.set_gmsh_option_if_available(
            gmsh, "Mesh.MeshSizeExtendFromBoundary", 0
        )
        gmsh_support.set_gmsh_option_if_available(gmsh, "Mesh.MeshSizeFromPoints", 0)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
        gmsh.option.setNumber("Mesh.MshFileVersion", 4.1)
        gmsh.option.setNumber("Mesh.Binary", 0)
        surface_algorithm_id = SURFACE_ALGORITHM_IDS[args.surface_algorithm]
        if surface_algorithm_id is not None:
            gmsh.option.setNumber("Mesh.Algorithm", surface_algorithm_id)
        algorithm_id = ALGORITHM_IDS[args.algorithm]
        if algorithm_id is not None:
            gmsh.option.setNumber("Mesh.Algorithm3D", algorithm_id)

        # Generating the surface mesh separately leaves a useful artifact when
        # the subsequent tetrahedralization rejects the volume boundary.
        gmsh.model.mesh.generate(2)
        gmsh.write(str(output_dir / "boundary.msh"))
        volume_path = output_dir / "volume.msh"
        if volume_path.exists():
            volume_path.unlink()
        try:
            gmsh.model.mesh.generate(3)
            volume_mesh = _volume_mesh_summary(gmsh)
            if not volume_mesh.get("element_counts"):
                mesh_error = "Gmsh completed without generating any 3D elements"
            else:
                gmsh.write(str(volume_path))
        except Exception as exc:
            mesh_error = str(exc)

        log_lines = list(gmsh.logger.get())
        (output_dir / "gmsh.log").write_text(
            "\n".join(log_lines) + "\n", encoding="utf-8"
        )
        if mesh_error:
            surface_tags_from_error = sorted(
                {
                    int(match)
                    for match in SURFACE_TAG_PATTERN.findall(mesh_error)
                }
            )

        all_volumes = [tag for _, tag in gmsh.model.getEntities(3)]
        all_surfaces = [tag for _, tag in gmsh.model.getEntities(2)]
        problem_surfaces = [
            _surface_details(gmsh, tag)
            for tag in surface_tags_from_error
            if tag in all_surfaces
        ]
        shared_problem_curves = []
        if len(problem_surfaces) > 1:
            shared_problem_curves = sorted(
                set.intersection(
                    *(set(surface["boundary_curves"]) for surface in problem_surfaces)
                )
            )
        report = {
            "step_path": str(step_path),
            "gmsh_version": getattr(gmsh, "GMSH_API_VERSION", "unknown"),
            "size_mm": args.size_mm,
            "algorithm": args.algorithm,
            "surface_algorithm": args.surface_algorithm,
            "surface_refinements": refinements,
            "mesh_error": mesh_error,
            "volume_tags": all_volumes,
            "surface_count": len(all_surfaces),
            "volume_mesh": volume_mesh,
            "surface_tags_from_error": surface_tags_from_error,
            "overlapping_facets": _overlapping_facets(gmsh, log_lines),
            "problem_surfaces": problem_surfaces,
            "shared_problem_curves": [
                _curve_details(gmsh, tag) for tag in shared_problem_curves
            ],
            "pipeline_model_info": {
                key: value
                for key, value in model_info.items()
                if key != "timings"
            },
        }
        _write_json(output_dir / "report.json", report)
    finally:
        gmsh.logger.stop()
        gmsh.finalize()

    print(f"Diagnostics written to {output_dir}")
    if mesh_error:
        print(f"3D mesh failed: {mesh_error}")
        if surface_tags_from_error:
            print(
                "Surfaces named by Gmsh: "
                + ", ".join(str(tag) for tag in surface_tags_from_error)
            )
        return 2
    print("3D mesh succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
