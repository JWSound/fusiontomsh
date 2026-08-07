import json
import unittest
from copy import deepcopy
from unittest.mock import mock_open, patch

from msh_settings import load_mesh_settings, save_fem_settings


class FEMSettingsTests(unittest.TestCase):
    def test_loads_saved_per_body_fem_groups(self):
        settings_path = "settings.json"
        saved_data = {
            "fem": {
                "last_msh_path": "C:/meshes/body.msh",
                "last_body_key": "body-token",
                "algo_3d": "Frontal",
                "default_size": 3.0,
                "boundary_size": 1.0,
                "by_body": {
                    "body-token": {
                        "body_token": "body-token",
                        "body_name": "Enclosure",
                        "msh_path": "C:/meshes/body.msh",
                        "algo_3d": "Frontal",
                        "default_size": 3.0,
                        "boundary_groups": [{
                            "name": "Radiator",
                            "size": 1.0,
                            "face_tokens": ["face-a", "face-b"],
                        }],
                    }
                },
            }
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(saved_data))):
            fem = load_mesh_settings(settings_path)["fem"]

            self.assertEqual(fem["last_body_key"], "body-token")
            self.assertEqual(fem["by_body"]["body-token"]["body_name"], "Enclosure")
            self.assertEqual(
                fem["by_body"]["body-token"]["boundary_groups"][0],
                {"name": "Radiator", "size": 1.0, "face_tokens": ["face-a", "face-b"]},
            )

    def test_save_fem_settings_preserves_surface_export_settings(self):
        settings_path = "settings.json"
        existing_settings = {
            "last_msh_path": "C:/meshes/surface.msh",
            "algo_2d": "Delaunay",
            "defaults": {"size": 2.0, "curvature": 4},
            "seam_blending": False,
            "by_body": {"Body": {"size": 2.0, "curvature": 4}},
            "fem": {"last_body_key": "", "by_body": {}},
        }
        with (
            patch("msh_settings.load_mesh_settings", return_value=deepcopy(existing_settings)),
            patch("msh_settings.save_mesh_settings") as save_mesh_settings_mock,
        ):
            save_fem_settings(settings_path, {
                "last_msh_path": "C:/meshes/volume.msh",
                "last_body_key": "body-token",
                "algo_3d": "Automatic",
                "default_size": 4.0,
                "boundary_size": 2.0,
                "by_body": {},
            })

            settings = save_mesh_settings_mock.call_args.args[1]
            self.assertEqual(settings["last_msh_path"], "C:/meshes/surface.msh")
            self.assertEqual(settings["algo_2d"], "Delaunay")
            self.assertFalse(settings["seam_blending"])
            self.assertEqual(settings["fem"]["last_msh_path"], "C:/meshes/volume.msh")


if __name__ == "__main__":
    unittest.main()
