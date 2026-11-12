"""Lit, procedural scenery for the music renderer (Blender 4.2+)."""

import math

import bpy
import numpy as np
from mathutils import Vector


def surface_material(name, primary, secondary, metallic=0.65):
    """Keep highlights, shadows and fine relief visible instead of emitting white."""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    shader = tree.nodes.get("Principled BSDF")
    shader.inputs["Metallic"].default_value = metallic
    shader.inputs["Roughness"].default_value = 0.26
    shader.inputs["Coat Weight"].default_value = 0.35
    shader.inputs["Coat Roughness"].default_value = 0.18
    shader.inputs["Emission Color"].default_value = primary
    shader.inputs["Emission Strength"].default_value = 0.035
    texture = tree.nodes.new("ShaderNodeTexNoise")
    texture.inputs["Scale"].default_value = 3.8
    texture.inputs["Detail"].default_value = 3.0
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.22
    ramp.color_ramp.elements[0].color = secondary
    ramp.color_ramp.elements[1].position = 0.78
    ramp.color_ramp.elements[1].color = primary
    tree.links.new(texture.outputs["Fac"], ramp.inputs["Fac"])
    tree.links.new(ramp.outputs["Color"], shader.inputs["Base Color"])
    fine = tree.nodes.new("ShaderNodeTexNoise")
    fine.inputs["Scale"].default_value = 95
    bump = tree.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.14
    bump.inputs["Distance"].default_value = 0.035
    tree.links.new(fine.outputs["Fac"], bump.inputs["Height"])
    tree.links.new(bump.outputs["Normal"], shader.inputs["Normal"])
    return material


def finish_mesh(obj, material, smooth=True):
    obj.data.materials.append(material)
    if obj.type == "MESH":
        for polygon in obj.data.polygons:
            polygon.use_smooth = smooth
    return obj


def create_sculpture(shape, material, location):
    if shape == "particles":
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4, radius=0.72, location=location)
        obj = bpy.context.object
        texture = bpy.data.textures.new("Sculpted core", type="CLOUDS")
        texture.noise_scale = 0.3
        displace = obj.modifiers.new("Breathing surface", "DISPLACE")
        displace.texture = texture
        displace.strength = 0.16
        # Linked meshes keep the flowing cloud inexpensive to render.
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=1)
        bead = bpy.context.object
        finish_mesh(bead, material)
        mesh = bead.data
        bpy.data.objects.remove(bead, do_unlink=True)
        for index in range(64):
            child = bpy.data.objects.new(f"Orbit bead {index:02d}", mesh)
            bpy.context.collection.objects.link(child)
            child.parent = obj
            child["orbit_index"] = index
            child.scale = (0.035 + 0.065 * ((index * 17) % 23) / 23,) * 3
    elif shape == "crystal":
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=1, location=location)
        obj = bpy.context.object
        for vertex in obj.data.vertices:
            vertex.co.z *= 1.65
            vertex.co.x *= 0.75
        bevel = obj.modifiers.new("Polished edges", "BEVEL")
        bevel.width = 0.035
        bevel.segments = 3
        for index in range(7):
            angle = index * math.tau / 7
            child = bpy.data.objects.new(f"Crystal shard {index}", obj.data.copy())
            bpy.context.collection.objects.link(child)
            child.parent = obj
            child.location = (1.1 * math.cos(angle), 1.1 * math.sin(angle), -0.35)
            child.rotation_euler = (0.35 * math.sin(angle), -0.35 * math.cos(angle), angle)
            child.scale = (0.4, 0.4, 0.5 + 0.12 * math.sin(index))
            finish_mesh(child, material, False)
    elif shape == "torus":
        bpy.ops.mesh.primitive_torus_add(major_radius=1.05, minor_radius=0.29,
                                        major_segments=96, minor_segments=32, location=location)
        obj = bpy.context.object
        twist = obj.modifiers.new("Liquid twist", "SIMPLE_DEFORM")
        twist.deform_method = "TWIST"
        twist.deform_axis = "X"
        twist.angle = 0.7
    elif shape == "fluid_metaball":
        # Distinct family names prevent metaballs in adjacent sections merging.
        meta = bpy.data.metaballs.new("Liquid sculpture")
        meta.resolution = 0.12
        meta.render_resolution = 0.08
        obj = bpy.data.objects.new("Liquid " + material.name, meta)
        bpy.context.collection.objects.link(obj)
        obj.location = location
        for index in range(6):
            element = meta.elements.new()
            element.radius = 0.95 if index else 1.3
    elif shape == "wireframe_grid":
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=36, y_subdivisions=36,
                                       size=3.0, location=location)
        obj = bpy.context.object
        for vertex in obj.data.vertices:
            x, y = vertex.co.x, vertex.co.y
            vertex.co.z = 0.38 * math.sin(x * 2.5) * math.cos(y * 2)
        wire = obj.modifiers.new("Woven lattice", "WIREFRAME")
        wire.thickness = 0.018
        bevel = obj.modifiers.new("Rounded threads", "BEVEL")
        bevel.width = 0.012
        bevel.segments = 2
        twist = obj.modifiers.new("Liquid twist", "SIMPLE_DEFORM")
        twist.deform_method = "TWIST"
        twist.angle = 0.4
    else:
        raise ValueError(f"unsupported primary_shape: {shape}")
    return finish_mesh(obj, material, shape != "crystal")


def animate_sculpture(obj, seconds, energy, frame):
    modifier = obj.modifiers.get("Breathing surface")
    if modifier:
        modifier.strength = 0.12 + 0.18 * energy
        modifier.keyframe_insert("strength", frame=frame)
    twist = obj.modifiers.get("Liquid twist")
    if twist:
        twist.angle = 0.7 * math.sin(seconds * 0.45) + 0.55 * energy
        twist.keyframe_insert("angle", frame=frame)
    if obj.type == "META":
        for index, element in enumerate(obj.data.elements):
            angle = seconds * 0.55 + index * math.tau / 5
            radius = 0 if index == 0 else 0.68 + 0.2 * math.sin(seconds + index)
            element.co = (radius * math.cos(angle), radius * math.sin(angle),
                          0.45 * math.sin(angle * 1.7 + index))
            element.keyframe_insert("co", frame=frame)
    # Four-frame sampling is enough for smooth trajectories; audio envelopes
    # on the parent are still sampled at every video frame.
    if frame % 4 == 0:
        for child in obj.children:
            if "orbit_index" not in child:
                continue
            index = child["orbit_index"]
            angle = index * 2.399963 + seconds * (0.25 + (index % 3) * 0.08)
            z = 1 - 2 * (index + 0.5) / 64
            radius = (1.25 + 0.14 * math.sin(seconds + index)) * (1 + 0.08 * energy)
            latitude = math.sqrt(1 - z * z)
            child.location = (radius * latitude * math.cos(angle),
                              radius * latitude * math.sin(angle), radius * z)
            child.keyframe_insert("location", frame=frame)


def area_light(name, location, color, power, size):
    light = bpy.data.lights.new(name, "AREA")
    light.energy, light.color, light.shape, light.size = power, color, "DISK", size
    obj = bpy.data.objects.new(name, light)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    obj.rotation_euler = (-Vector(location)).to_track_quat("-Z", "Y").to_euler()
    return light


def add_stage():
    area_light("Warm key softbox", (2, -4, 6), (1.0, 0.82, 0.59), 1500, 5)
    area_light("Cool rim softbox", (-4, 1, 3), (0.35, 0.72, 1.0), 1900, 4)
    area_light("Pearl fill softbox", (4, 3, 1), (1.0, 0.94, 0.82), 1100, 3)
    floor = surface_material("Rippled midnight teal", (0.035, 0.095, 0.11, 1),
                             (0.009, 0.025, 0.035, 1), metallic=0.45)
    shader = floor.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Roughness"].default_value = 0.3
    shader.inputs["Emission Strength"].default_value = 0
    bpy.ops.mesh.primitive_plane_add(size=200, location=(0, 0, -2.1))
    obj = bpy.context.object
    obj.name = "Reflective stage"
    finish_mesh(obj, floor)


def add_ribbons_and_dust(analysis, fps, accent, dust):
    frames = analysis["frames"]
    # Open, rising paths replace the flat, concentric orbital outlines.
    for index in range(3):
        curve = bpy.data.curves.new(f"Flowing ribbon {index}", "CURVE")
        curve.dimensions = "3D"
        curve.bevel_depth = 0.009 if index != 1 else 0.016
        curve.bevel_resolution = 3
        spline = curve.splines.new("POLY")
        spline.points.add(159)
        for point_index, point in enumerate(spline.points):
            u = point_index / 159
            angle = u * math.tau * 1.2 + index * 2.1
            radius = 1.85 + 0.25 * math.sin(u * math.tau + index)
            point.co = (radius * math.cos(angle), radius * math.sin(angle),
                        -1.55 + 3.3 * u, 1)
            point.radius = 0.15 + 0.85 * math.sin(math.pi * u) ** 0.6
        obj = bpy.data.objects.new(curve.name, curve)
        bpy.context.collection.objects.link(obj)
        curve.materials.append(accent)
        for frame in range(0, frames + 3, 4):
            f = min(frame, frames - 1)
            obj.rotation_euler.z = f / fps * (0.10 + index * 0.025)
            obj.scale = (1 + 0.035 * float(analysis["pulse"][f]),) * 3
            obj.keyframe_insert("rotation_euler", frame=f)
            obj.keyframe_insert("scale", frame=f)
    rng = np.random.default_rng(42)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=1)
    prototype = bpy.context.object
    finish_mesh(prototype, dust)
    mesh = prototype.data
    bpy.data.objects.remove(prototype, do_unlink=True)
    for layer in range(3):
        root = bpy.data.objects.new(f"Drifting dust layer {layer}", None)
        bpy.context.collection.objects.link(root)
        for index in range(70):
            mote = bpy.data.objects.new(f"Mote {layer}-{index}", mesh)
            bpy.context.collection.objects.link(mote)
            mote.parent = root
            mote.location = (rng.uniform(-6, 6), rng.uniform(-6, 6), rng.uniform(-1.7, 6))
            mote.scale = (float(rng.uniform(0.008, 0.035)),) * 3
        for frame in range(0, frames + 5, 6):
            f = min(frame, frames - 1)
            t = f / fps
            root.rotation_euler.z = t * (0.025 + layer * 0.012)
            root.location.z = 0.18 * math.sin(t * 0.4 + layer)
            root.keyframe_insert("rotation_euler", frame=f)
            root.keyframe_insert("location", frame=f)
    strength = dust.node_tree.nodes["Emission"].inputs["Strength"]
    for frame in range(frames):
        strength.default_value = 0.7 + 1.1 * float(analysis["high"][frame])
        strength.keyframe_insert("default_value", frame=frame)
