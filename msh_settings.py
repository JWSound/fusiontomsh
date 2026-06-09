import json


DEFAULT_BODY_SIZE = 1.5
DEFAULT_BODY_CURVATURE = 0
DEFAULT_SEAM_BLENDING_ENABLED = True
DEFAULT_ALGO_2D = "Automatic"

ALGO_2D_NAMES = ("Automatic", "MeshAdapt", "Delaunay", "Frontal-Delaunay")


def sanitize_body_size_settings(size_val, curvature):
    safe_size = float(size_val) if size_val is not None else DEFAULT_BODY_SIZE
    safe_curvature = int(curvature) if curvature is not None else DEFAULT_BODY_CURVATURE

    if safe_size <= 0:
        safe_size = 1e-6
    if safe_curvature < 0:
        safe_curvature = 0
    if safe_curvature > 100:
        safe_curvature = 100

    return safe_size, safe_curvature


def sanitize_body_settings(min_val, max_val, curvature):
    size_val = min_val if min_val is not None else max_val
    safe_size, safe_curvature = sanitize_body_size_settings(size_val, curvature)
    return safe_size, safe_size, safe_curvature


def body_size_from_settings(settings, default_size):
    if not isinstance(settings, dict):
        return default_size
    return settings.get("size", settings.get("min", default_size))


def load_mesh_settings(settings_path):
    settings = {
        "last_msh_path": "",
        "algo_2d": DEFAULT_ALGO_2D,
        "defaults": {
            "size": DEFAULT_BODY_SIZE,
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
        default_size, default_curvature = sanitize_body_size_settings(
            body_size_from_settings(defaults, DEFAULT_BODY_SIZE),
            defaults.get("curvature", DEFAULT_BODY_CURVATURE)
        )
        settings["defaults"] = {
            "size": default_size,
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
                body_size, body_curvature = sanitize_body_size_settings(
                    body_size_from_settings(body_values, default_size),
                    body_values.get("curvature", default_curvature)
                )
                cleaned_by_body[body_name] = {
                    "size": body_size,
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
