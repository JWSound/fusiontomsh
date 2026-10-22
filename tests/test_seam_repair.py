"""CAD/mesh regressions; skipped when the optional Gmsh runtime is unavailable."""

from collections import Counter
import os
import tempfile
import unittest
from unittest.mock import patch

import gmsh_model
import seam_repair

try:
    import gmsh
except (ImportError, OSError):
    gmsh = None


@unittest.skipIf(gmsh is None, 'Gmsh runtime is not installed')
class PlanarSeamRepairTests(unittest.TestCase):
    def setUp(self):
        gmsh.initialize()
        gmsh.option.setNumber('General.Terminal', 0)
        gmsh.model.add('seam_regression')

    def tearDown(self):
        gmsh.finalize()

    def make_geometry(self, deviation=.005, endpoint_shift=0):
        occ = gmsh.model.occ
        a = occ.addPoint(10, 0, 0)
        b = occ.addPoint(8, 8, 0)
        c = occ.addPoint(0, 10, 0)
        rim = occ.addBSpline([a, b, c])
        side = next(tag for dim, tag in occ.extrude([(1, rim)], 0, 0, 5) if dim == 2)
        # Independent spline, as produced by separately exported Fusion bodies.
        x = occ.addPoint(10 + endpoint_shift, 0, 0)
        middle = occ.addPoint(8 + deviation, 8 + deviation, 0)
        y = occ.addPoint(0, 10 + endpoint_shift, 0)
        origin = occ.addPoint(0, 0, 0)
        cap_rim = occ.addBSpline([x, middle, y])
        line_a = occ.addLine(y, origin)
        line_b = occ.addLine(origin, x)
        wire = occ.addCurveLoop([cap_rim, line_a, line_b])
        cap = occ.addPlaneSurface([wire])
        occ.synchronize()
        return [[(2, side)], [(2, cap)]], rim, cap

    def mesh_and_check_seam(self, bodies, normals):
        mapped = gmsh_model.fragment_body_entities(gmsh, bodies)
        surfaces = {i: gmsh_model.boundary_surfaces(gmsh, values) for i, values in mapped.items()}
        self.assertEqual(len(surfaces[1]), 1, 'repair must avoid fragmented cap slivers')
        for index, normal in normals.items():
            seam_repair.restore_cap_orientation(gmsh, surfaces[index], normal)
        gmsh.option.setNumber('Mesh.MeshSizeMin', .5)
        gmsh.option.setNumber('Mesh.MeshSizeMax', 1)
        gmsh.model.mesh.generate(2)
        node_tags, xyz, _ = gmsh.model.mesh.getNodes()
        points = {int(t): tuple(xyz[3*i:3*i+3]) for i, t in enumerate(node_tags)}
        edges = Counter()
        for surface_list in surfaces.values():
            for surface in surface_list:
                types, _, nodes = gmsh.model.mesh.getElements(2, surface)
                for kind, connectivity in zip(types, nodes):
                    self.assertEqual(kind, 2)
                    for i in range(0, len(connectivity), 3):
                        a, b, c = map(int, connectivity[i:i+3])
                        edges.update(tuple(sorted(e)) for e in ((a, b), (b, c), (c, a)))
        self.assertFalse(any(count > 2 for count in edges.values()))
        # The synthetic shell is intentionally open at z=5 and on x/y=0.
        # Every other edge on the repaired z=0 perimeter must have two facets.
        for (a, b), count in edges.items():
            if max(abs(points[v][2]) for v in (a, b)) < 1e-7:
                symmetry = any(max(abs(points[v][axis]) for v in (a, b)) < 1e-7
                               for axis in (0, 1))
                if not symmetry:
                    self.assertEqual(count, 2)
        # The cap's generated facets must preserve the original CAD normal.
        normal = normals[1]
        _, _, blocks = gmsh.model.mesh.getElements(2, surfaces[1][0])
        for block in blocks:
            for i in range(0, len(block), 3):
                a, b, c = [points[int(t)] for t in block[i:i+3]]
                u, v = [b[j]-a[j] for j in range(3)], [c[j]-a[j] for j in range(3)]
                cross = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
                self.assertGreater(sum(x*y for x, y in zip(normal, cross)), 0)

    def test_near_coincident_spline_builds_one_conformal_cap(self):
        bodies, rim, old_cap = self.make_geometry()
        repaired, normals = seam_repair.repair_planar_caps(gmsh, bodies)
        self.assertEqual(set(normals), {1})
        new_cap = repaired[1][0][1]
        self.assertNotEqual(old_cap, new_cap)
        self.assertIn((1, rim), gmsh.model.getBoundary([(2, new_cap)], oriented=False))
        self.mesh_and_check_seam(repaired, normals)

    def test_endpoint_drift_reattaches_straight_symmetry_edges(self):
        bodies, _, _ = self.make_geometry(endpoint_shift=.002)
        repaired, normals = seam_repair.repair_planar_caps(gmsh, bodies)
        self.assertEqual(set(normals), {1})
        self.mesh_and_check_seam(repaired, normals)

    def test_larger_gap_is_unchanged(self):
        bodies, _, _ = self.make_geometry(deviation=.1)
        before = gmsh.model.getEntities()
        repaired, normals = seam_repair.repair_planar_caps(gmsh, bodies)
        self.assertEqual(repaired, bodies)
        self.assertFalse(normals)
        self.assertEqual(before, gmsh.model.getEntities())

    def test_ambiguous_rims_are_unchanged(self):
        bodies, _, _ = self.make_geometry()
        duplicate = gmsh.model.occ.copy(bodies[0])
        gmsh.model.occ.synchronize()
        bodies.append(duplicate)
        before = gmsh.model.getEntities()
        repaired, normals = seam_repair.repair_planar_caps(gmsh, bodies)
        self.assertEqual(repaired, bodies)
        self.assertFalse(normals)
        self.assertEqual(before, gmsh.model.getEntities())

    def test_solids_are_unchanged(self):
        volume = gmsh.model.occ.addBox(0, 0, 0, 10, 10, 10)
        gmsh.model.occ.synchronize()
        bodies = [[(3, volume)]]
        repaired, normals = seam_repair.repair_planar_caps(gmsh, bodies)
        self.assertEqual(repaired, bodies)
        self.assertFalse(normals)

    def test_cap_with_hole_is_unchanged(self):
        bodies, _, cap = self.make_geometry()
        occ = gmsh.model.occ
        wires, _ = occ.getCurveLoops(cap)
        hole = occ.addCircle(2, 2, 0, .25)
        hole_wire = occ.addCurveLoop([hole])
        replacement = occ.addPlaneSurface([int(wires[0]), hole_wire])
        occ.remove([(2, cap)], recursive=False)
        occ.synchronize()
        bodies[1] = [(2, replacement)]
        repaired, normals = seam_repair.repair_planar_caps(gmsh, bodies)
        self.assertEqual(repaired, bodies)
        self.assertFalse(normals)

    def test_failed_rebuild_preserves_original_geometry(self):
        bodies, _, _ = self.make_geometry(endpoint_shift=.002)
        before = gmsh.model.getEntities()
        with patch.object(gmsh.model.occ, 'addPlaneSurface', side_effect=RuntimeError('test failure')):
            repaired, normals = seam_repair.repair_planar_caps(gmsh, bodies)
        self.assertEqual(repaired, bodies)
        self.assertFalse(normals)
        self.assertEqual(before, gmsh.model.getEntities())

    def test_full_export_path_repairs_separately_imported_steps(self):
        bodies, _, _ = self.make_geometry()
        with tempfile.TemporaryDirectory() as directory:
            paths = [os.path.join(directory, name + '.step') for name in ('exterior', 'cap')]
            gmsh.option.setNumber('Geometry.OCCExportOnlyVisible', 1)
            for path, entities in zip(paths, bodies):
                gmsh.model.setVisibility(gmsh.model.getEntities(), 0)
                gmsh.model.setVisibility(entities, 1, recursive=True)
                gmsh.write(path)
            gmsh.clear()
            gmsh_model.build_gmsh_export_model(gmsh, paths, ['ext', 'interface'], [3.2, .4])
            groups = {gmsh.model.getPhysicalName(d, t): list(gmsh.model.getEntitiesForPhysicalGroup(d, t))
                      for d, t in gmsh.model.getPhysicalGroups()}
            self.assertEqual(set(groups), {'ext', 'interface'})
            self.assertEqual(len(groups['interface']), 1)
            shared = gmsh_model.collect_shared_curve_adjacency(gmsh, {0: groups['ext'], 1: groups['interface']})
            self.assertTrue(shared)
            gmsh.clear()
            with patch.object(seam_repair, 'repair_planar_caps') as repair:
                gmsh_model.build_gmsh_export_model(
                    gmsh, paths, ['ext', 'interface'], [3.2, .4], conformal_topology=False
                )
                repair.assert_not_called()


if __name__ == '__main__':
    unittest.main()
