import unittest
from pathlib import Path
from unittest.mock import patch

import fem_patch_repair as repair
import gmsh_support


class IntersectionTests(unittest.TestCase):
    def setUp(self):
        self.a = ((0., 0., 0.), (1., 0., 0.), (0., 1., 0.))

    def test_shared_edge_allowed(self):
        b = ((0., 0., 0.), (1., 0., 0.), (0., -1., 0.))
        self.assertFalse(repair._intersects(self.a, b, list(self.a[:2])))

    def test_coplanar_overlap_rejected_even_with_shared_edge(self):
        b = ((0., 0., 0.), (1., 0., 0.), (0.2, 0.2, 0.))
        self.assertTrue(repair._intersects(self.a, b, list(self.a[:2])))

    def test_crossing_triangle_rejected(self):
        b = ((.2, .2, -1.), (.2, .2, 1.), (.8, .2, 0.))
        self.assertTrue(repair._intersects(self.a, b, []))

    def test_shared_vertex_does_not_hide_intersection(self):
        b = (self.a[0], (.3, .3, -1.), (.3, .3, 1.))
        self.assertTrue(repair._intersects(self.a, b, [self.a[0]]))

    def test_separate_triangles_allowed(self):
        b = ((2., 0., 0.), (3., 0., 0.), (2., 1., 0.))
        self.assertFalse(repair._intersects(self.a, b, []))


class BoundaryLoopTests(unittest.TestCase):
    def test_large_opening_refused(self):
        edges = {(1, 2): [(1, 1, 2)], (2, 3): [(1, 2, 3)],
                 (3, 4): [(1, 3, 4)], (1, 4): [(1, 4, 1)]}
        with self.assertRaisesRegex(RuntimeError, 'isolated triangle'):
            repair._loops(edges)

    def test_nonmanifold_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'non-manifold'):
            repair._loops({(1, 2): [(1, 1, 2), (2, 2, 1), (3, 1, 2)]})

    def test_orientation_conflict_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'orientation'):
            repair._loops({(1, 2): [(1, 1, 2), (2, 1, 2)]})


class CADPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.gmsh = gmsh_support.import_gmsh_from_wheelhouse(str(root/'wheelhouse'), str(root/'.gmsh_wheels'))
        if cls.gmsh is None:
            raise unittest.SkipTest('Bundled Gmsh unavailable')

    def setUp(self):
        g = self.gmsh
        g.initialize()
        self.addCleanup(g.finalize)
        g.option.setNumber('General.Terminal', 0)
        g.model.add('patch regression')
        self.volume = g.model.occ.addBox(0, 0, 0, 1, 1, 1)
        g.model.occ.synchronize()
        for _, c in g.model.getEntities(1):
            g.model.mesh.setTransfiniteCurve(c, 2)
        self.faces = [t for _, t in g.model.getEntities(2)]
        for face in self.faces:
            g.model.mesh.setTransfiniteSurface(face)
        self.face = self.faces[0]
        g.model.addPhysicalGroup(3, [self.volume], name='air')
        g.model.addPhysicalGroup(2, [self.face], name='driver')
        g.model.addPhysicalGroup(2, self.faces[1:], name='walls')
        g.model.mesh.generate(2)
        self.groups = repair.physical_groups(g)
        self.count = len(repair._triangles(g))

    def remove_corner(self):
        g = self.gmsh
        _, tags, _ = g.model.mesh.getElements(2, self.face)
        g.model.mesh.removeElements(2, self.face, [int(tags[0][0])])

    def test_intact_solid_needs_no_repair(self):
        self.assertEqual(repair.repair_boundary(self.gmsh, 1), [])
        self.assertEqual(len(repair._triangles(self.gmsh)), self.count)

    def test_corner_repair_preserves_groups_and_tetrahedral_boundary(self):
        self.remove_corner()
        result = repair.repair_boundary(self.gmsh, 1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['surface'], self.face)
        self.assertEqual(len(repair._triangles(self.gmsh)), self.count)
        self.assertEqual(repair.physical_groups(self.gmsh), self.groups)
        self.gmsh.model.mesh.generate(3)
        repair.validate_repaired_volume(self.gmsh, self.groups)
        self.assertEqual(repair.repair_boundary(self.gmsh, 1), [])

    def test_entire_missing_face_not_filled(self):
        self.gmsh.model.mesh.removeElements(2, self.face)
        with self.assertRaisesRegex(RuntimeError, 'isolated triangle'):
            repair.repair_boundary(self.gmsh, 1)

    def test_patch_outside_trim_refused_without_mutation(self):
        self.remove_corner()
        with patch.object(self.gmsh.model, 'isInside', return_value=0):
            with self.assertRaisesRegex(RuntimeError, 'trimmed CAD boundary'):
                repair.repair_boundary(self.gmsh, 1)
        self.assertEqual(len(repair._triangles(self.gmsh)), self.count-1)

    def test_excessive_deviation_refused_without_mutation(self):
        self.remove_corner()
        original = self.gmsh.model.getClosestPoint
        def displaced(*args):
            coords, uv = original(*args)
            return [v+0.1 for v in coords], uv
        with patch.object(self.gmsh.model, 'getClosestPoint', side_effect=displaced):
            with self.assertRaisesRegex(RuntimeError, 'departs from the CAD'):
                repair.repair_boundary(self.gmsh, 1)
        self.assertEqual(len(repair._triangles(self.gmsh)), self.count-1)

    def test_large_patch_refused(self):
        self.remove_corner()
        with self.assertRaisesRegex(RuntimeError, 'size or shape'):
            repair.repair_boundary(self.gmsh, .1)

    def test_ambiguous_cad_segment_refused_without_mutation(self):
        self.remove_corner()
        g = self.gmsh
        missing = {key for key, values in repair._edges(repair._triangles(g)).items()
                   if len(values) == 1}
        original = g.model.mesh.getElements
        selected_curve = None
        selected_data = None
        for _, curve in g.model.getEntities(1):
            data = original(1, curve)
            if data[2] and tuple(sorted(map(int, data[2][0][:2]))) in missing:
                selected_curve, selected_data = curve, data
                break
        other_curve = next(c for _, c in g.model.getEntities(1) if c != selected_curve)
        def ambiguous(dim=-1, tag=-1):
            return selected_data if (dim, tag) == (1, other_curve) else original(dim, tag)
        with patch.object(g.model.mesh, 'getElements', side_effect=ambiguous):
            with self.assertRaisesRegex(RuntimeError, 'ambiguous CAD edge ownership'):
                repair.repair_boundary(g, 1)
        self.assertEqual(len(repair._triangles(g)), self.count-1)

    def test_boundary_mismatch_rejected_after_tetrahedralization(self):
        self.remove_corner()
        repair.repair_boundary(self.gmsh, 1)
        self.gmsh.model.mesh.generate(3)
        self.remove_corner()
        with self.assertRaisesRegex(RuntimeError, 'does not match'):
            repair.validate_repaired_volume(self.gmsh, self.groups)

    def test_changed_group_rejected_after_tetrahedralization(self):
        self.remove_corner()
        repair.repair_boundary(self.gmsh, 1)
        self.gmsh.model.mesh.generate(3)
        self.gmsh.model.addPhysicalGroup(2, [self.face], name='unexpected')
        with self.assertRaisesRegex(RuntimeError, 'physical groups'):
            repair.validate_repaired_volume(self.gmsh, self.groups)

    def test_nonpositive_quality_rejected(self):
        self.remove_corner()
        repair.repair_boundary(self.gmsh, 1)
        self.gmsh.model.mesh.generate(3)
        with patch.object(self.gmsh.model.mesh, 'getElementQualities',
                          side_effect=lambda tags, metric: [0.]*len(tags)):
            with self.assertRaisesRegex(RuntimeError, 'degenerate tetrahedra'):
                repair.validate_repaired_volume(self.gmsh, self.groups)


if __name__ == '__main__':
    unittest.main()
