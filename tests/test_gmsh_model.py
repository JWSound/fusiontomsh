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

    def test_only_shared_seams_seed_coarser_body(self):
        gmsh, _ = self.make_gmsh()
        self.assertEqual(gmsh_model.collect_seam_targets(
            gmsh, {0: [1], 1: [2], 2: [3]}, [3.2, .4, .2]),
            {0: {4.0: {10}}})

    def test_disabled_blending_adds_no_curve_sources(self):
        gmsh, field = self.make_gmsh()
        gmsh_model.add_background_mesh_fields(
            gmsh, {0: [1], 1: [2]}, [3.2, .4], False
        )
        self.assertFalse(any(option == 'CurvesList'
                             for _, option, _ in field.number_list_values))


class SurfaceDistanceTests(unittest.TestCase):
    def test_fold_and_disconnected_coincident_nodes(self):
        points = {0: (0, 0, 0), 1: (10, 0, 0), 2: (10, 1, 0),
                  3: (0, 1, 0), 4: (0, 0, 0), 5: (1, 0, 0), 6: (0, 1, 0)}
        distances = gmsh_model.surface_distances(
            points, [(0, 1, 2), (1, 2, 3), (4, 5, 6)], {0}, 100)
        self.assertGreater(distances[3], 19)
        self.assertNotIn(4, distances)
        self.assertNotIn(3, gmsh_model.surface_distances(
            points, [(0, 1, 2), (1, 2, 3)], {0}, 15))


class SurfaceDistanceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        import gmsh_support
        root = Path(__file__).resolve().parents[1]
        cls.g = gmsh_support.import_gmsh_from_wheelhouse(
            str(root/'wheelhouse'), str(root/'.gmsh_wheels'))
        if cls.g is None:
            raise unittest.SkipTest('Gmsh unavailable')

    def test_folded_shell_keeps_nearby_outer_panel_coarse(self):
        g = self.g
        g.initialize()
        try:
            g.option.setNumber('General.Terminal', 0)
            g.model.add('folded')
            geo = g.model.geo
            points = {}
            edges = {}
            def face(coords):
                ids = [points.setdefault(p, geo.addPoint(*p)) if p not in points
                       else points[p] for p in coords]
                boundary = []
                for a, b in zip(ids, ids[1:] + ids[:1]):
                    if (b, a) in edges:
                        boundary.append(-edges[b, a])
                    else:
                        if (a, b) not in edges:
                            edges[a, b] = geo.addLine(a, b)
                        boundary.append(edges[a, b])
                return geo.addPlaneSurface([geo.addCurveLoop(boundary)])
            inner = face([(0,0,0), (20,0,0), (20,100,0), (0,100,0)])
            rim = face([(0,100,0), (20,100,0), (20,100,2), (0,100,2)])
            outer = face([(0,100,2), (20,100,2), (20,0,2), (0,0,2)])
            fine = face([(0,-10,0), (20,-10,0), (20,0,0), (0,0,0)])
            geo.synchronize()
            g.option.setNumber('Mesh.MeshSizeFactor', 1)
            gmsh_model.add_background_mesh_fields(g, {0:[inner,rim,outer],1:[fine]}, [2,.2], True)
            self.assertEqual(g.option.getNumber('Mesh.MeshSizeFactor'), 1)
            self.assertEqual(len(g.model.mesh.getNodes()[0]), 0)
            g.option.setNumber('Mesh.MeshSizeFromPoints', 0)
            g.option.setNumber('Mesh.MeshSizeExtendFromBoundary', 0)
            g.model.mesh.generate(2)
            # Inspect the actual final triangles, not just the generated fields.
            import math
            import statistics
            ids, xyz, _ = g.model.mesh.getNodes()
            coords = {int(t): xyz[3*i:3*i+3] for i,t in enumerate(ids)}
            def near_seam_edges(surface):
                _, _, arrays = g.model.mesh.getElements(2, surface)
                lengths = []
                for nodes in arrays:
                    for i in range(0, len(nodes), 3):
                        tri = list(map(int, nodes[i:i+3]))
                        if sum(coords[n][1] for n in tri)/3 < 15:
                            lengths.extend(math.dist(coords[a], coords[b])
                                           for a,b in zip(tri, tri[1:]+tri[:1]))
                return statistics.median(lengths)
            self.assertGreater(near_seam_edges(outer), 10)
            self.assertLess(near_seam_edges(inner), 6)
        finally:
            g.finalize()


if __name__ == '__main__':
    unittest.main()
