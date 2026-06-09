import json


DEFAULT_BODY_MIN = 1.5
DEFAULT_BODY_MAX = 3.0
DEFAULT_BODY_CURVATURE = 0
DEFAULT_SEAM_BLENDING_ENABLED = True
DEFAULT_ALGO_2D = "Automatic"

ALGO_2D_NAMES = ("Automatic", "MeshAdapt", "Delaunay", "Frontal-Delaunay")


def sanitize_body_settings(min_val, max_val, curvature):
    safe_min = float(min_val) if min_val is not None else DEFAULT_BODY_MIN
    safe_max = float(max_val) if max_val is not None else DEFAULT_BODY_MAX
    safe_curvature = int(curvature) if curvature is not None else DEFAULT_BODY_CURVATURE

    if safe_min <= 0:
        safe_min = 1e-6
    if safe_max < safe_min:
        safe_max = safe_min
    if safe_curvature < 0:
        safe_curvature = 0
    if safe_curvature > 100:
        safe_curvature = 100

    return safe_min, safe_max, safe_curvature


def load_mesh_settings(settings_path):
    settings = {
        "last_msh_path": "",
        "algo_2d": DEFAULT_ALGO_2D,
        "defaults": {
            "min": DEFAULT_BODY_MIN,
            "max": DEFAULT_BODY_MAX,
            "curvature": DEFAULT_BODY_CURVATURE,
        },
        "seam_blending": DEFAULT_SEAM_BLENDING_ENABLED,
        "by_body": {}
    }

    try:
        with open(settings_path, "r", encoding="utf-8") as settings_file:
            loaded = json.load(settings_file)
    except Exception:
        return settings

    try:
        if isinstance(loaded, dict):
            last_msh_path = loaded.get("last_msh_path", "")
            if isinstance(last_msh_path, str):
                settings["last_msh_path"] = last_msh_path

            algo_2d = loaded.get("algo_2d", DEFAULT_ALGO_2D)
            if algo_2d in ALGO_2D_NAMES:
                settings["algo_2d"] = algo_2d

        defaults = loaded.get("defaults", {}) if isinstance(loaded, dict) else {}
        default_min, default_max, default_curvature = sanitize_body_settings(
            defaults.get("min", DEFAULT_BODY_MIN),
            defaults.get("max", DEFAULT_BODY_MAX),
            defaults.get("curvature", DEFAULT_BODY_CURVATURE)
        )
        settings["defaults"] = {
            "min": default_min,
            "max": default_max,
            "curvature": default_curvature,
        }

        seam_blending = loaded.get("seam_blending", DEFAULT_SEAM_BLENDING_ENABLED) if isinstance(loaded, dict) else DEFAULT_SEAM_BLENDING_ENABLED
        settings["seam_blending"] = seam_blending if isinstance(seam_blending, bool) else DEFAULT_SEAM_BLENDING_ENABLED

        loaded_by_body = loaded.get("by_body", {}) if isinstance(loaded, dict) else {}
        if isinstance(loaded_by_body, dict):
            cleaned_by_body = {}
            for body_name, body_values in loaded_by_body.items():
                if not isinstance(body_name, str) or not isinstance(body_values, dict):
                    continue
                body_min, body_max, body_curvature = sanitize_body_settings(
                    body_values.get("min", default_min),
                    body_values.get("max", default_max),
                    body_values.get("curvature", default_curvature)
                )
                cleaned_by_body[body_name] = {
                    "min": body_min,
                    "max": body_max,
                    "curvature": body_curvature,
                }
            settings["by_body"] = cleaned_by_body
    except Exception:
        return settings

    return settings


def save_mesh_settings(settings_path, settings_data):
    try:
        with open(settings_path, "w", encoding="utf-8") as settings_file:
            json.dump(settings_data, settings_file, indent=2)
    except Exception:
        pass


def algo_text_to_id(algo_text):
    # 1: MeshAdapt, 2: Automatic, 5: Delaunay, 6: Frontal-Delaunay
    algo_map = {"Automatic": 2, "MeshAdapt": 1, "Delaunay": 5, "Frontal-Delaunay": 6}
    return algo_map.get(algo_text, 2)
