import argparse
from pathlib import Path
import sys

import bpy


def main():
    args = parse_args()
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    clear_scene()
    import_model(input_path)
    obj = join_mesh_objects()
    if obj is None:
        raise SystemExit(f"Nenhuma malha encontrada em {input_path}")

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    apply_voxel_remesh(obj, args.voxel_size, args.adaptivity)
    cleanup_mesh(obj)
    export_model(output_path)


def parse_args():
    parser = argparse.ArgumentParser(description="Repara uma malha com Voxel Remesh no Blender.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--voxel-size", type=float, default=0.01)
    parser.add_argument("--adaptivity", type=float, default=0.0)
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(argv)


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def import_model(path):
    suffix = path.suffix.lower()
    if suffix in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif suffix == ".stl":
        bpy.ops.wm.stl_import(filepath=str(path))
    elif suffix == ".obj":
        bpy.ops.wm.obj_import(filepath=str(path))
    else:
        raise SystemExit(f"Formato não suportado: {suffix}")


def join_mesh_objects():
    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not mesh_objects:
        return None

    bpy.ops.object.select_all(action="DESELECT")
    for obj in mesh_objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_objects[0]
    bpy.ops.object.join()
    return bpy.context.object


def apply_voxel_remesh(obj, voxel_size, adaptivity):
    modifier = obj.modifiers.new("Voxel Repair", "REMESH")
    modifier.mode = "VOXEL"
    modifier.voxel_size = voxel_size
    modifier.adaptivity = adaptivity
    modifier.use_smooth_shade = False
    bpy.ops.object.modifier_apply(modifier=modifier.name)


def cleanup_mesh(obj):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=0.00001)
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")


def export_model(path):
    suffix = path.suffix.lower()
    if suffix == ".stl":
        bpy.ops.wm.stl_export(filepath=str(path), export_selected_objects=True)
    elif suffix in {".glb", ".gltf"}:
        bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_selection=True)
    else:
        raise SystemExit(f"Formato de saída não suportado: {suffix}")


if __name__ == "__main__":
    main()
