"""Run with Blender to render a neutral, generated workflow gallery."""

from pathlib import Path
import math

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "images" / "synthetic-workflow.png"


def material(name, color, metallic=0.0, roughness=0.45):
    value = bpy.data.materials.new(name)
    value.diffuse_color = (*color, 1)
    value.use_nodes = True
    nodes = value.node_tree.nodes
    nodes.clear()
    node = nodes.new("ShaderNodeBsdfPrincipled")
    output = nodes.new("ShaderNodeOutputMaterial")
    value.node_tree.links.new(node.outputs["BSDF"], output.inputs["Surface"])
    node.inputs["Base Color"].default_value = (*color, 1)
    node.inputs["Metallic"].default_value = metallic
    node.inputs["Roughness"].default_value = roughness
    return value


def look_at(obj, target):
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def pedestal(x, label):
    bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=2.25, depth=0.24, location=(x, 0, 0.12))
    bpy.context.object.data.materials.append(material(f"base-{label}", (0.17, 0.21, 0.23), metallic=0.1, roughness=0.36))
    bpy.ops.object.text_add(location=(x, -2.55, 0.18), rotation=(math.radians(72), 0, 0))
    text = bpy.context.object
    text.data.body = label
    text.data.align_x = "CENTER"
    text.data.size = 0.42
    text.data.extrude = 0.012
    text.data.materials.append(material(f"text-{label}", (0.88, 0.91, 0.92), roughness=0.6))


def raw_mesh(x):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=1.65, location=(x, 0, 1.85))
    obj = bpy.context.object
    for index, vertex in enumerate(obj.data.vertices):
        vertex.co *= 1 + 0.10 * math.sin(index * 1.73)
    obj.scale = (0.88, 1.06, 1.18)
    obj.data.materials.append(material("raw", (0.36, 0.55, 0.64), roughness=0.68))


def segmented_mesh(x):
    colors = [(0.88, 0.55, 0.22), (0.28, 0.62, 0.52), (0.35, 0.45, 0.68)]
    for index, (z, radius) in enumerate(((0.72, 1.42), (1.65, 1.62), (2.75, 1.30))):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=radius, location=(x, 0, z + 0.35))
        obj = bpy.context.object
        obj.scale.z = 0.58 if index != 1 else 0.78
        obj.data.materials.append(material(f"segment-{index}", colors[index], roughness=0.42))


def print_ready_mesh(x):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=96, ring_count=48, radius=1.55, location=(x, 0, 1.85))
    obj = bpy.context.object
    obj.scale = (0.88, 0.88, 1.25)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bevel = obj.modifiers.new("print-softening", "BEVEL")
    bevel.width = 0.08
    bevel.segments = 3
    obj.data.materials.append(material("ready", (0.24, 0.57, 0.43), roughness=0.34))
    bpy.ops.mesh.primitive_cylinder_add(vertices=96, radius=1.42, depth=0.20, location=(x, 0, 0.42))
    bpy.context.object.data.materials.append(material("ready-base", (0.18, 0.43, 0.34), roughness=0.4))


def main():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    world = bpy.context.scene.world
    world.color = (0.025, 0.032, 0.035)
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.025, 0.032, 0.035, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.38

    for x, label in ((-4.8, "01 GENERATED"), (0, "02 COLOR SPLIT"), (4.8, "03 PRINT READY")):
        pedestal(x, label)
    raw_mesh(-4.8)
    segmented_mesh(0)
    print_ready_mesh(4.8)

    bpy.ops.mesh.primitive_plane_add(size=36, location=(0, 0, -0.02))
    bpy.context.object.data.materials.append(material("floor", (0.065, 0.078, 0.082), roughness=0.82))
    for location, energy, size in (((0, -4, 10), 1500, 7), ((-8, 3, 5), 900, 5), ((8, 2, 4), 850, 4)):
        bpy.ops.object.light_add(type="AREA", location=location)
        light = bpy.context.object
        light.data.energy = energy
        light.data.shape = "DISK"
        light.data.size = size
        look_at(light, (0, 0, 1.5))

    bpy.ops.object.camera_add(location=(14.4, -21.9, 10.6))
    camera = bpy.context.object
    look_at(camera, (0, 0, 1.45))
    camera.data.lens = 54
    bpy.context.scene.camera = camera
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 1400
    scene.render.resolution_y = 650
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.render.filepath = str(OUTPUT)
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.look = "AgX - Medium High Contrast"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main()
