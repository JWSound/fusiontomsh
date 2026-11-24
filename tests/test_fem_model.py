import math
import statistics
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import fem_model


class _FakeField:
    def __init__(self):
        self.added = []
        self.number_values = []
        self.number_list_values = []
        self.background_field = None

    def add(self, field_type):
        self.added.append(field_type)
        return len(self.added)

    def setNumber(self, field_id, option, value):
        self.number_values.append((field_id, option, value))

    def setNumbers(self, field_id, option, values):
        self.number_list_values.append((field_id, option, values))

    def setAsBackgroundMesh(self, field_id):
        self.background_field = field_id


class _FakeMesh:
    def __init__(self):
        self.field = _FakeField()


class _FakeModel:
    def __init__(self):
        self.mesh = _FakeMesh()


class _FakeGmsh:
    def __init__(self):
        self.model = _FakeModel()


class SurfaceSizeFieldTests(unittest.TestCase):
    def test_coarse_face_blends_to_body_and_equal_face_caps_overlap(self):
        gmsh = _FakeGmsh()

        fem_model._add_surface_size_fields(
            gmsh,
            [
                {"surface_tags": [1], "size": 1.6},
                {"surface_tags": [2], "size": 2.0},
            ],
            default_size=1.6,
        )

        field = gmsh.model.mesh.field
        self.assertEqual(field.added, ["Distance", "Threshold", "Distance", "Threshold", "Min"])
        self.assertIn((2, "SizeMin", 16.0), field.number_values)
        self.assertIn((2, "SizeMax", 20.0), field.number_values)
        self.assertIn((4, "SizeMin", 20.0), field.number_values)
        self.assertIn((4, "SizeMax", 16.0), field.number_values)
        self.assertIn((5, "FieldsList", [2, 4]), field.number_list_values)
        self.assertEqual(field.background_field, 5)

    def test_unmatched_coarse_group_preserves_body_size(self):
        gmsh = Mock()
        field = gmsh.model.mesh.field
        field.add.return_value = 1
        fem_model._add_surface_size_fields(
            gmsh, [{"surface_tags": [], "size": 3.6}], default_size=2.0
        )
        field.add.assert_called_once_with("MathEval")
        field.setString.assert_called_once_with(1, "F", "20.0")
        field.setAsBackgroundMesh.assert_called_once_with(1)

    def test_keeps_smaller_surface_refinement(self):
        gmsh = _FakeGmsh()

        fem_model._add_surface_size_fields(
            gmsh,
            [{"surface_tags": [7, 3], "size": 0.8}],
            default_size=1.6,
        )

        self.assertEqual(gmsh.model.mesh.field.added, ["Distance", "Threshold"])
        self.assertIn((1, "FacesList", [3, 7]), gmsh.model.mesh.field.number_list_values)
        self.assertEqual(gmsh.model.mesh.field.background_field, 2)


class ExportedSizeTests(unittest.TestCase):
    def test_local_coarse_face_on_20_mm_body(self):
        try:
            import gmsh
        except ImportError:
            self.skipTest("Gmsh is required for mesh measurements")

        with tempfile.TemporaryDirectory() as directory:
            step = str(Path(directory) / "cube.step")
            mesh = str(Path(directory) / "cube.msh")
            gmsh.initialize()
            try:
                gmsh.model.occ.addBox(0, 0, 0, 500, 500, 500)
                gmsh.model.occ.synchronize()
                groups = [
                    {"name": name, "size": size,
                     "face_descriptors": [fem_model._surface_descriptor(gmsh, face)]}
                    for name, size, face in [("Cone", 2, 1), ("Interface", 3.6, 2),
                                             ("Surround", 2, 3)]
                ]
                gmsh.write(step)
            finally:
                gmsh.finalize()

            def build():
                result = fem_model.build_fem_export_model(gmsh, step, "Body", 2, groups)
                self.assertEqual(result["unmatched_groups"], [])

            fem_model.gmsh_support.write_gmsh_mesh(
                gmsh, mesh, build, 2, 3.6, 0, mesh_dimension=3, msh_file_version=4.1
            )
            gmsh.initialize()
            try:
                gmsh.open(mesh)
                self.assertGreater(len(gmsh.model.mesh.getElementsByType(4)[0]), 0)
                ids, xyz, _ = gmsh.model.mesh.getNodes()
                points = dict(zip(ids, zip(xyz[::3], xyz[1::3], xyz[2::3])))
                for dim, tag in gmsh.model.getPhysicalGroups(2):
                    name = gmsh.model.getPhysicalName(dim, tag)
                    edges = set()
                    for face in gmsh.model.getEntitiesForPhysicalGroup(dim, tag):
                        _, nodes = gmsh.model.mesh.getElementsByType(2, face)
                        for a, b, c in zip(nodes[::3], nodes[1::3], nodes[2::3]):
                            edges.update(tuple(sorted(pair)) for pair in [(a, b), (b, c), (c, a)])
                    median = statistics.median(math.dist(points[a], points[b]) for a, b in edges)
                    expected = 36 if name == "Interface" else 20
                    self.assertAlmostEqual(median, expected, delta=3, msg=name)
            finally:
                gmsh.finalize()


class FusionFaceMatchingTests(unittest.TestCase):
    def test_matches_curved_face_by_area_and_bbox_not_arbitrary_surface_point(self):
        cylinder_bbox = (
            -220.0000001,
            -177.4584732,
            -152.4584732,
            -200.0099999,
            -22.5415268,
            2.4584732,
        )
        fusion_face = {
            "area": 9728.8519433,
            # This is a valid point on the cylinder but is far from its center
            # of mass, as Fusion's pointOnFace is allowed to be.
            "sample_point": (-210.0, -22.5415269, -75.0),
            "bbox": cylinder_bbox,
        }
        surfaces = [
            {
                "tag": 12,
                "area": 9062.3393668,
                "centroid": (-191.5, -100.0, -75.0),
                "bbox": (
                    -200.0100001,
                    -196.9049211,
                    -171.9049211,
                    -183.0099999,
                    -3.0950789,
                    21.9049211,
                ),
            },
            {
                "tag": 45,
                "area": 9728.8519433,
                "centroid": (-210.005, -100.0, -75.0),
                "bbox": cylinder_bbox,
            },
        ]

        self.assertEqual(
            fem_model.match_fusion_face_to_surfaces(fusion_face, surfaces),
            [45],
        )

    def test_legacy_centroid_descriptor_still_matches_without_bbox(self):
        fusion_face = {
            "area": 100.0,
            "centroid": (1.0, 2.0, 3.0),
            "bbox": None,
        }
        surfaces = [
            {
                "tag": 7,
                "area": 100.0,
                "centroid": (1.0, 2.0, 3.0),
                "bbox": None,
            }
        ]

        self.assertEqual(
            fem_model.match_fusion_face_to_surfaces(fusion_face, surfaces),
            [7],
        )


if __name__ == "__main__":
    unittest.main()
