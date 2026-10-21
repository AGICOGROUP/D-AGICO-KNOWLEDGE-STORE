"""Blender subprocess entry point for a single static model thumbnail."""

import sys

import bpy
from mathutils import Vector


def main():
    source, output, suffix = sys.argv[sys.argv.index("--") + 1 :]
    if suffix == ".blend":
        bpy.ops.wm.open_mainfile(filepath=source, load_ui=False, use_scripts=False)
    else:
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        if suffix == ".glb":
            bpy.ops.import_scene.gltf(filepath=source)
        elif suffix == ".stl":
            bpy.ops.wm.stl_import(filepath=source)
        else:
            raise ValueError("Unsupported model format")

    scene = bpy.context.scene
    meshes = [obj for obj in scene.objects if obj.type == "MESH" and not obj.hide_render]
    if not meshes:
        raise ValueError("Model has no visible mesh")
    corners = [obj.matrix_world @ Vector(corner) for obj in meshes for corner in obj.bound_box]
    lower = Vector(tuple(min(point[axis] for point in corners) for axis in range(3)))
    upper = Vector(tuple(max(point[axis] for point in corners) for axis in range(3)))
    center = (lower + upper) / 2
    dimensions = upper - lower
    major_axis = max(range(3), key=lambda axis: dimensions[axis])
    direction = [Vector((0.35, -1, 0.65)), Vector((1, -0.35, 0.65)),
                 Vector((1, -0.8, 0.35))][major_axis].normalized()

    camera_data = bpy.data.cameras.new("Knowledge base preview camera")
    camera_data.type = "ORTHO"
    camera_data.clip_end = max(dimensions.length * 10, 1000)
    camera = bpy.data.objects.new(camera_data.name, camera_data)
    scene.collection.objects.link(camera)
    camera.location = center + direction * max(dimensions.length, 1) * 2
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    rotation = camera.rotation_euler.to_matrix()
    right, up = rotation @ Vector((1, 0, 0)), rotation @ Vector((0, 1, 0))
    width = max((point - center).dot(right) for point in corners) - min(
        (point - center).dot(right) for point in corners)
    height = max((point - center).dot(up) for point in corners) - min(
        (point - center).dot(up) for point in corners)
    camera_data.ortho_scale = max(width, height, 0.01) * 1.22
    scene.camera = camera

    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.display.shading.background_type = "VIEWPORT"
    scene.display.shading.background_color = (0.96, 0.97, 0.99)
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.render.resolution_x = 512
    scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = output
    bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main()
