#!/usr/bin/env python3
"""Render an audio-reactive abstract video in headless Blender 4.2+.

Example:
  Blender --background --factory-startup -noaudio --python-exit-code 1 \
    --python scripts/render_scene.py -- --lyrics-json lyrics.json \
    --analysis-wav analysis.wav --output silent_video.mp4
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

import bpy
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from audio_analysis import load_audio  # noqa: E402

FPS = 24
WIDTH = 1080
HEIGHT = 1920


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    if "--" not in argv:
        raise SystemExit("missing -- separator before script arguments")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lyrics-json", required=True, type=Path)
    parser.add_argument("--analysis-wav", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=int(os.environ.get("RENDER_SAMPLES", "64")))
    return parser.parse_args(argv[argv.index("--") + 1 :])


def hex_to_rgba(value: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    if len(value) != 6:
        raise ValueError(f"invalid hex color: {value}")
    try:
        red, green, blue = (int(value[index : index + 2], 16) / 255 for index in (0, 2, 4))
    except ValueError as error:
        raise ValueError(f"invalid hex color: {value}") from error
    return red, green, blue, alpha


def emissive_mat(name: str, color: tuple[float, float, float, float], strength: float = 4.0):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree
    nodes.nodes.clear()
    output = nodes.nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.nodes.new("ShaderNodeEmission")
    emission.name = "Emission"
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = strength
    nodes.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return material


def create_shape(shape: str, material, location: tuple[float, float, float]):
    if shape == "particles":
        # Reliable baseline: an ico sphere + wireframe. Geometry Nodes can replace
        # this branch later without changing the JSON contract.
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=0.8, location=location)
        obj = bpy.context.object
        modifier = obj.modifiers.new("Wire", "WIREFRAME")
        modifier.thickness = 0.02
    elif shape == "crystal":
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=1.0, location=location)
        obj = bpy.context.object
    elif shape == "torus":
        bpy.ops.mesh.primitive_torus_add(location=location, major_radius=1.0, minor_radius=0.3)
        obj = bpy.context.object
    elif shape == "fluid_metaball":
        metaball = bpy.data.metaballs.new("Meta")
        metaball.resolution = 0.2
        obj = bpy.data.objects.new("MetaObj", metaball)
        bpy.context.collection.objects.link(obj)
        element = metaball.elements.new()
        element.radius = 1.2
        obj.location = location
    elif shape == "wireframe_grid":
        bpy.ops.mesh.primitive_grid_add(
            x_subdivisions=20,
            y_subdivisions=20,
            size=4.0,
            location=location,
        )
        obj = bpy.context.object
        modifier = obj.modifiers.new("Wire", "WIREFRAME")
        modifier.thickness = 0.03
    else:
        raise ValueError(f"unsupported primary_shape: {shape}")

    obj.data.materials.append(material)
    if obj.type == "MESH":
        for polygon in obj.data.polygons:
            polygon.use_smooth = shape != "crystal"
    return obj


def section_ranges(sections: list[dict[str, Any]], frames: int) -> list[tuple[int, int]]:
    """Split the timeline into inclusive, gapless section ranges."""
    if not sections:
        raise ValueError("lyrics.json must contain at least one section")
    ranges = []
    for index in range(len(sections)):
        start = index * frames // len(sections)
        end = (index + 1) * frames // len(sections) - 1
        ranges.append((start, max(start, end)))
    return ranges


def add_atmosphere(data: dict[str, Any], analysis: dict[str, Any]) -> None:
    """Layer fine orbital rings and a deterministic field of luminous motes."""
    frames = analysis["frames"]
    palette = data["sections"][0]["visual"]["palette"]
    accent = emissive_mat("Orbit gold", hex_to_rgba(palette[1]), 2.0)
    for index in range(3):
        bpy.ops.mesh.primitive_torus_add(major_radius=1.8 + index * 0.35,
                                       minor_radius=0.012, major_segments=96, minor_segments=8)
        ring = bpy.context.object
        ring.data.materials.append(accent)
        for frame in range(frames):
            pulse = float(analysis["pulse"][frame])
            scale = 1 + 0.09 * pulse
            ring.scale = (scale,) * 3
            ring.rotation_euler = (0.4 + index * 0.6, frame / FPS * 0.09, index + frame / FPS * 0.04)
            ring.keyframe_insert("scale", frame=frame)
            ring.keyframe_insert("rotation_euler", frame=frame)
    dust = emissive_mat("Stardust", hex_to_rgba(palette[-1]), 1.5)
    root = bpy.data.objects.new("Star field", None)
    bpy.context.collection.objects.link(root)
    rng = np.random.default_rng(42)
    for _ in range(100):
        direction = rng.normal(size=3)
        direction /= np.linalg.norm(direction)
        position = direction * rng.uniform(3.0, 7.0)
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=float(rng.uniform(0.008, 0.025)), location=position)
        star = bpy.context.object
        star.parent = root
        star.data.materials.append(dust)
    strength = dust.node_tree.nodes["Emission"].inputs["Strength"]
    for frame in range(frames):
        root.rotation_euler = (0, 0, frame / FPS * 0.012)
        root.keyframe_insert("rotation_euler", frame=frame)
        strength.default_value = 1.2 + 1.5 * float(analysis["high"][frame])
        strength.keyframe_insert("default_value", frame=frame)
    scene = bpy.context.scene
    # Blender 5 uses a compositor node group; Blender 4 uses scene.node_tree.
    scene.use_nodes = True
    if hasattr(scene, "compositing_node_group"):
        tree = bpy.data.node_groups.new("Music glow", "CompositorNodeTree")
        scene.compositing_node_group = tree
        tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        output = tree.nodes.new("NodeGroupOutput")
    else:
        tree = scene.node_tree
        tree.nodes.clear()
        output = tree.nodes.new("CompositorNodeComposite")
    source = tree.nodes.new("CompositorNodeRLayers")
    glow = tree.nodes.new("CompositorNodeGlare")
    if "Type" in glow.inputs:
        glow.inputs["Type"].default_value = "Fog Glow"
        glow.inputs["Quality"].default_value = "High"
    else:
        glow.glare_type = "FOG_GLOW"
        glow.quality = "HIGH"
    tree.links.new(source.outputs["Image"], glow.inputs["Image"])
    tree.links.new(glow.outputs["Image"], output.inputs["Image"])


def build_scene(data: dict[str, Any], analysis: dict[str, Any], samples: int) -> None:
    frames = analysis["frames"]
    if frames < 1:
        raise ValueError("audio analysis produced no video frames")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps = FPS

    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    background = world.node_tree.nodes["Background"]
    background.inputs["Color"].default_value = (0.004, 0.004, 0.008, 1)
    background.inputs["Strength"].default_value = 1.0
    scene.world = world

    sections = data["sections"]
    ranges = section_ranges(sections, frames)

    for index, (section, (start, end)) in enumerate(zip(sections, ranges)):
        visual = section["visual"]
        palette = visual["palette"]
        material = emissive_mat(f"Section_{index}", hex_to_rgba(palette[0]))
        obj = create_shape(visual["primary_shape"], material, (0, 0, 0))
        emission = material.node_tree.nodes["Emission"].inputs["Strength"]
        intensity = float(visual["motion_intensity"])
        transition = min(FPS, max(2, (end - start + 1) // 4))
        start = max(0, start - transition)
        end = min(frames - 1, end + transition)

        keyframes = sorted(set(range(start, end + 1)) | {start, end})
        for frame in keyframes:
            local = frame - start
            remaining = end - frame
            fade_in = min(1.0, local / transition)
            fade_out = min(1.0, remaining / transition)
            base = max(0.0, min(fade_in, fade_out))

            low = float(analysis["low"][frame])
            mid = float(analysis["mid"][frame])
            high = float(analysis["high"][frame])
            rms = float(analysis["rms"][frame])
            pulse = float(analysis["pulse"][frame])
            energy = (0.35 * low + 0.25 * mid + 0.15 * rms + 0.25 * pulse) * intensity
            scale = base * (0.6 + 0.8 * energy)

            obj.scale = (scale, scale, scale)
            obj.keyframe_insert("scale", frame=frame)
            emission.default_value = base * (2.0 + 4.0 * energy + 3.0 * high * intensity)
            emission.keyframe_insert("default_value", frame=frame)

            if frame % 4 == 0 or frame == end:
                obj.rotation_euler = (
                    mid * intensity * 0.35,
                    local / FPS * 0.25 * intensity,
                    local / FPS * 0.3 * (0.5 + intensity),
                )
                obj.keyframe_insert("rotation_euler", frame=frame)

        # The first and last keys both have scale zero. Constant extrapolation
        # therefore keeps the object invisible outside its own section.
        obj.scale = (0, 0, 0)
        emission.default_value = 0.0

    target = bpy.data.objects.new("Target", None)
    bpy.context.collection.objects.link(target)
    camera_data = bpy.data.cameras.new("Camera")
    camera = bpy.data.objects.new("Camera", camera_data)
    bpy.context.collection.objects.link(camera)
    scene.camera = camera
    constraint = camera.constraints.new("TRACK_TO")
    constraint.target = target
    constraint.track_axis = "TRACK_NEGATIVE_Z"
    constraint.up_axis = "UP_Y"

    camera_data.lens = 38
    radius, height = 10.5, 3.5
    camera_frames = sorted(set(range(0, frames, 4)) | {frames - 1})
    for frame in camera_frames:
        theta = frame / FPS * 0.035
        # Continuous orbit/dolly avoids jumps when visual sections change.
        current_radius = radius + 0.5 * math.sin(frame / FPS * 0.08)
        camera.location = (
            current_radius * math.cos(theta),
            current_radius * math.sin(theta),
            height + 0.3 * math.sin(frame / FPS * 0.06),
        )
        camera.keyframe_insert("location", frame=frame)

    add_atmosphere(data, analysis)
    scene.render.resolution_x = WIDTH
    scene.render.resolution_y = HEIGHT
    scene.render.resolution_percentage = 100
    engine_ids = {
        item.identifier
        for item in scene.render.bl_rna.properties["engine"].enum_items
    }
    if "BLENDER_EEVEE_NEXT" in engine_ids:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    elif "BLENDER_EEVEE" in engine_ids:
        scene.render.engine = "BLENDER_EEVEE"
    else:
        raise RuntimeError(f"EEVEE is unavailable; render engines: {sorted(engine_ids)}")
    if hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
        scene.eevee.taa_render_samples = samples

    # Blender 5.x splits image/video output into media_type first; 4.x does not.
    if hasattr(scene.render.image_settings, "media_type"):
        scene.render.image_settings.media_type = "VIDEO"
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "HIGH"
    scene.frame_start = 0
    scene.frame_end = frames - 1


def resolve_rendered_output(output: Path, started_at: float) -> None:
    """Accept Blender variants that append a frame range to movie filenames."""
    if output.exists() and output.stat().st_size > 0:
        return
    candidates = [
        path
        for path in output.parent.glob(f"{output.stem}*{output.suffix}")
        if path.is_file() and path.stat().st_size > 0 and path.stat().st_mtime >= started_at - 2
    ]
    if candidates:
        newest = max(candidates, key=lambda path: path.stat().st_mtime)
        newest.replace(output)
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"render produced no non-empty file: {output}")


def main() -> None:
    args = parse_args()
    if args.samples < 1:
        raise SystemExit("--samples must be positive")

    with args.lyrics_json.open(encoding="utf-8") as handle:
        data = json.load(handle)
    analysis = load_audio(args.analysis_wav, FPS)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Never let a stale video from an earlier run masquerade as this render.
    args.output.unlink(missing_ok=True)

    build_scene(data, analysis, args.samples)
    scene = bpy.context.scene
    scene.render.filepath = str(args.output.resolve())

    started_at = time.time()
    bpy.ops.render.render(animation=True)
    resolve_rendered_output(args.output, started_at)
    print(f"OK silent video -> {args.output}")


if __name__ == "__main__":
    main()
