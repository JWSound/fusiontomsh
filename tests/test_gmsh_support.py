import unittest
from unittest.mock import MagicMock, patch

import gmsh_support


class _FakeMesh:
    def getElements(self):
        return [2, 4], [[1, 2, 3], [4, 5]], [[], []]


class _FakeModel:
    def __init__(self):
        self.mesh = _FakeMesh()

    def getPhysicalGroups(self):
        return [(3, 1), (2, 2), (2, 3)]


class _FakeGmsh:
    def __init__(self):
        self.model = _FakeModel()


class MeshStatisticsTests(unittest.TestCase):
    def test_collects_total_elements_and_physical_groups(self):
        statistics = gmsh_support.collect_mesh_statistics(_FakeGmsh())

        self.assertEqual(statistics["element_count"], 5)
        self.assertEqual(statistics["physical_group_count"], 3)


class VolumeExportValidationTests(unittest.TestCase):
    def setUp(self):
        # Boundary repair is exercised with real CAD/meshes in its own tests.
        self.repair = patch('gmsh_support.fem_patch_repair.repair_boundary', return_value=[])
        self.repair.start()
        self.addCleanup(self.repair.stop)

    def _export(self, gmsh, dimension=3):
        return gmsh_support.write_gmsh_mesh(
            gmsh, "existing.msh", lambda: None, 0.1, 0.1, 0,
            mesh_dimension=dimension,
        )

    def test_surface_only_result_is_rejected_before_write(self):
        gmsh = MagicMock()
        gmsh.model.getEntities.return_value = [(3, 1)]
        gmsh.model.mesh.getElements.return_value = ([], [], [])
        with self.assertRaisesRegex(RuntimeError, "no 3D elements"):
            self._export(gmsh)
        gmsh.write.assert_not_called()
        gmsh.finalize.assert_called_once()

    def test_partially_meshed_volumes_are_rejected(self):
        gmsh = MagicMock()
        gmsh.model.getEntities.return_value = [(3, 1), (3, 2)]
        gmsh.model.mesh.getElements.side_effect = [([4], [[1]], [[]]), ([], [], [])]
        with self.assertRaisesRegex(RuntimeError, "volume\\(s\\): 2"):
            self._export(gmsh)
        gmsh.write.assert_not_called()

    def test_missing_volume_is_rejected(self):
        gmsh = MagicMock()
        gmsh.model.getEntities.return_value = []
        with self.assertRaisesRegex(RuntimeError, "none imported"):
            self._export(gmsh)
        gmsh.write.assert_not_called()

    def test_valid_volume_is_written(self):
        gmsh = MagicMock()
        gmsh.model.getEntities.return_value = [(3, 1)]
        gmsh.model.mesh.getElements.return_value = ([4], [[1, 2]], [[]])
        self._export(gmsh)
        gmsh.write.assert_called_once_with("existing.msh")

    def test_surface_export_remains_supported(self):
        gmsh = MagicMock()
        gmsh.model.mesh.getElements.return_value = ([2], [[1]], [[]])
        self._export(gmsh, dimension=2)
        gmsh.write.assert_called_once_with("existing.msh")
        gmsh.model.getEntities.assert_not_called()

    def test_failed_repair_validation_never_writes(self):
        gmsh = MagicMock()
        gmsh.model.getEntities.return_value = [(3, 1)]
        gmsh.model.mesh.getElements.return_value = ([4], [[1]], [[]])
        with patch('gmsh_support.fem_patch_repair.repair_boundary', return_value=[{'surface': 1}]), \
             patch('gmsh_support.fem_patch_repair.validate_repaired_volume', side_effect=RuntimeError('invalid repaired mesh')):
            with self.assertRaisesRegex(RuntimeError, 'invalid repaired mesh'):
                self._export(gmsh)
        gmsh.write.assert_not_called()
        gmsh.finalize.assert_called_once()

    def test_repair_details_are_returned_only_after_validation(self):
        gmsh = MagicMock()
        gmsh.model.getEntities.return_value = [(3, 1)]
        gmsh.model.mesh.getElements.return_value = ([4], [[1]], [[]])
        repairs = [{'surface': 1, 'nodes': [1, 2, 3]}]
        with patch('gmsh_support.fem_patch_repair.repair_boundary', return_value=repairs), \
             patch('gmsh_support.fem_patch_repair.validate_repaired_volume') as validate:
            result = self._export(gmsh)
        self.assertEqual(result['repaired_patches'], repairs)
        validate.assert_called_once()
        gmsh.write.assert_called_once()


if __name__ == "__main__":
    unittest.main()
