import unittest

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


if __name__ == "__main__":
    unittest.main()
