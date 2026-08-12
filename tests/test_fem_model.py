import unittest

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
    def test_skips_equal_and_coarser_surface_sizes(self):
        gmsh = _FakeGmsh()

        fem_model._add_surface_size_fields(
            gmsh,
            [
                {"surface_tags": [1], "size": 1.6},
                {"surface_tags": [2], "size": 2.0},
            ],
            default_size=1.6,
        )

        self.assertEqual(gmsh.model.mesh.field.added, [])
        self.assertIsNone(gmsh.model.mesh.field.background_field)

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
