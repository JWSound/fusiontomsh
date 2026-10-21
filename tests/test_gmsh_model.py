import unittest
from types import SimpleNamespace

import gmsh_model
from test_fem_model import _FakeField


class SeamBlendingTests(unittest.TestCase):
    def make_gmsh(self):
        boundaries = {1: [(1, 10), (1, 11)],
                      2: [(1, 10), (1, 20), (1, 21)],
                      3: [(1, 30)]}
        field = _FakeField()
        model = SimpleNamespace(
            mesh=SimpleNamespace(field=field),
            getBoundary=lambda entities, **kwargs: [
                edge for _, tag in entities for edge in boundaries[tag]
            ],
        )
        return SimpleNamespace(model=model), field

    def test_partial_shared_seam_uses_whole_connected_fine_boundary(self):
        gmsh, field = self.make_gmsh()
        gmsh_model.add_background_mesh_fields(
            gmsh, {0: [1], 1: [2], 2: [3]}, [3.2, .4, .2], True
        )
        sources = [values for _, option, values in field.number_list_values
                   if option == 'CurvesList']
        self.assertEqual(sources, [[10, 20, 21]])
        # The unconnected finer body must not change this seam's size.
        self.assertIn((11, 'LcMin', 4.0), field.number_values)
        self.assertIn((11, 'LcMax', 32.0), field.number_values)
        self.assertIn((11, 'DistMax', 96.0), field.number_values)

    def test_disabled_blending_adds_no_curve_sources(self):
        gmsh, field = self.make_gmsh()
        gmsh_model.add_background_mesh_fields(
            gmsh, {0: [1], 1: [2]}, [3.2, .4], False
        )
        self.assertFalse(any(option == 'CurvesList'
                             for _, option, _ in field.number_list_values))


if __name__ == '__main__':
    unittest.main()
