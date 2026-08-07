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


if __name__ == "__main__":
    unittest.main()
