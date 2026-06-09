import glob
import os
import platform
import shutil
import sys
import zipfile


FUSION_TO_GMSH_LENGTH_SCALE = 10.0


def merge_tree(src_dir, dst_dir):
    if not os.path.isdir(src_dir):
        return

    os.makedirs(dst_dir, exist_ok=True)
    for root, dir_names, file_names in os.walk(src_dir):
        rel_root = os.path.relpath(root, src_dir)
        target_root = dst_dir if rel_root == "." else os.path.join(dst_dir, rel_root)
        os.makedirs(target_root, exist_ok=True)

        for dir_name in dir_names:
            os.makedirs(os.path.join(target_root, dir_name), exist_ok=True)

        for file_name in file_names:
            source_file = os.path.join(root, file_name)
            target_file = os.path.join(target_root, file_name)
            if os.path.exists(target_file):
                continue
            try:
                shutil.copy2(source_file, target_file)
            except PermissionError:
                if not os.path.exists(target_file):
                    raise


def normalize_gmsh_wheel_layout(extract_dir):
    normalized_marker = os.path.join(extract_dir, ".normalized")
    if os.path.isfile(normalized_marker):
        return

    data_roots = glob.glob(os.path.join(extract_dir, "*.data", "data"))
    for data_root in data_roots:
        merge_tree(data_root, extract_dir)

    try:
        with open(normalized_marker, "w", encoding="utf-8") as marker_file:
            marker_file.write("ok")
    except Exception:
        pass


def import_gmsh_from_wheelhouse(wheelhouse_dir, wheel_extract_dir):
    if not os.path.isdir(wheelhouse_dir):
        return None

    all_wheel_paths = sorted(
        glob.glob(os.path.join(wheelhouse_dir, "gmsh-*.whl")),
        reverse=True
    )

    machine = platform.machine().lower()
    wheel_paths = []
    fallback_wheels = []

    for wheel_path in all_wheel_paths:
        wheel_filename = os.path.basename(wheel_path).lower()

        if sys.platform.startswith("win"):
            if "win_amd64" in wheel_filename:
                wheel_paths.append(wheel_path)
            else:
                fallback_wheels.append(wheel_path)
        elif sys.platform == "darwin":
            if machine in ("arm64", "aarch64") and "macosx_12_0_arm64" in wheel_filename:
                wheel_paths.append(wheel_path)
            elif machine in ("x86_64", "amd64") and "macosx" in wheel_filename and "x86_64" in wheel_filename:
                wheel_paths.append(wheel_path)
            else:
                fallback_wheels.append(wheel_path)
        else:
            fallback_wheels.append(wheel_path)

    wheel_paths.extend(fallback_wheels)

    if not wheel_paths:
        return None

    os.makedirs(wheel_extract_dir, exist_ok=True)

    for wheel_path in wheel_paths:
        wheel_name = os.path.splitext(os.path.basename(wheel_path))[0]
        extract_dir = os.path.join(wheel_extract_dir, wheel_name)
        ready_marker = os.path.join(extract_dir, ".ready")

        if not os.path.isfile(ready_marker):
            if os.path.isdir(extract_dir):
                shutil.rmtree(extract_dir, ignore_errors=True)
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(wheel_path, "r") as wheel_zip:
                wheel_zip.extractall(extract_dir)
            with open(ready_marker, "w", encoding="utf-8") as marker_file:
                marker_file.write("ok")

        normalize_gmsh_wheel_layout(extract_dir)

        if sys.platform.startswith("win") and hasattr(os, "add_dll_directory"):
            for dll_dir in (extract_dir, os.path.join(extract_dir, "lib"), os.path.join(extract_dir, "bin")):
                if os.path.isdir(dll_dir):
                    try:
                        os.add_dll_directory(dll_dir)
                    except Exception:
                        pass

        sys.path.insert(0, extract_dir)
        try:
            import gmsh as vendored_gmsh
            return vendored_gmsh
        except Exception:
            try:
                sys.path.remove(extract_dir)
            except ValueError:
                pass

    return None


def gmsh_length(fusion_length):
    return float(fusion_length) * FUSION_TO_GMSH_LENGTH_SCALE


def set_gmsh_option_if_available(gmsh_module, name, value):
    try:
        gmsh_module.option.setNumber(name, value)
        return True
    except Exception:
        return False


def apply_global_mesh_size_limits(gmsh_module, min_size, max_size):
    set_gmsh_option_if_available(gmsh_module, "Mesh.MeshSizeMin", min_size)
    set_gmsh_option_if_available(gmsh_module, "Mesh.MeshSizeMax", max_size)

    # Older Gmsh builds used CharacteristicLength* names.
    set_gmsh_option_if_available(gmsh_module, "Mesh.CharacteristicLengthMin", min_size)
    set_gmsh_option_if_available(gmsh_module, "Mesh.CharacteristicLengthMax", max_size)


def install_global_min_mesh_size_callback(gmsh_module, min_size):
    def mesh_size_floor_callback(dim, tag, x, y, z, lc):
        return max(float(lc), min_size)

    try:
        gmsh_module.model.mesh.setSizeCallback(mesh_size_floor_callback)
    except Exception:
        pass


def write_gmsh_mesh(
    gmsh_module,
    msh_path,
    build_model,
    global_min_val,
    global_max_val,
    effective_global_curvature,
    algo_id,
):
    gmsh_module.initialize()
    try:
        build_model()

        global_min_size = gmsh_length(global_min_val)
        global_max_size = gmsh_length(global_max_val)

        apply_global_mesh_size_limits(gmsh_module, global_min_size, global_max_size)
        install_global_min_mesh_size_callback(gmsh_module, global_min_size)
        set_gmsh_option_if_available(gmsh_module, "Mesh.MeshSizeExtendFromBoundary", 0)
        set_gmsh_option_if_available(gmsh_module, "Mesh.MeshSizeFromPoints", 0)
        gmsh_module.option.setNumber("Mesh.Algorithm", algo_id)
        gmsh_module.option.setNumber("Mesh.MeshSizeFromCurvature", effective_global_curvature)
        gmsh_module.option.setNumber("Mesh.MshFileVersion", 2.2)
        gmsh_module.option.setNumber("Mesh.Binary", 0)

        gmsh_module.model.mesh.generate(2)
        gmsh_module.write(msh_path)
    finally:
        gmsh_module.finalize()
