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
from cinematic_visuals import (  # noqa: E402
    add_ribbons_and_dust, add_stage, animate_sculpture, create_sculpture, surface_material,
)

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
    parser.add_argument("--preview-seconds", type=float, help="Render only the opening seconds")
    parser.add_argument("--resolution-percentage", type=int, default=100)
    parser.add_argument("--still-frame", type=int, help="Render one frame to a PNG instead of video")
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
    # Palette values are sRGB; shader sockets expect scene-linear values.
    def linear(channel):
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
    return linear(red), linear(green), linear(blue), alpha


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
    return create_sculpture(shape, material, location)


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
    """Frame the sculpture with flowing light trails, depth and gentle bloom."""
    palette = data["sections"][0]["visual"]["palette"]
    accent = emissive_mat("Ribbon accent", hex_to_rgba(palette[1]), 1.8)
    dust = emissive_mat("Floating pollen", hex_to_rgba(palette[-1]), 1.2)
    add_ribbons_and_dust(analysis, FPS, accent, dust)
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
        if "Threshold" in glow.inputs:
            glow.inputs["Threshold"].default_value = 1.5
        if "Strength" in glow.inputs:
            glow.inputs["Strength"].default_value = 0.25
    else:
        glow.glare_type = "FOG_GLOW"
        glow.quality = "HIGH"
        glow.threshold = 1.5
        glow.mix = -0.85
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
    background.inputs["Color"].default_value = (0.055, 0.085, 0.12, 1)
    background.inputs["Strength"].default_value = 0.45
    scene.world = world
    scene.view_settings.view_transform = "AgX"
    add_stage()

    sections = data["sections"]
    ranges = section_ranges(sections, frames)

    for index, (section, (start, end)) in enumerate(zip(sections, ranges)):
        visual = section["visual"]
        palette = visual["palette"]
        material = surface_material(f"Section_{index}", hex_to_rgba(palette[0]),
                                    hex_to_rgba(palette[1]))
        obj = create_shape(visual["primary_shape"], material, (0, 0, 0))
        obj.name = f"Hero {index}: {visual['primary_shape']}"
        emission = material.node_tree.nodes["Principled BSDF"].inputs["Emission Strength"]
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
            base = base * base * (3 - 2 * base)

            low = float(analysis["low"][frame])
            mid = float(analysis["mid"][frame])
            high = float(analysis["high"][frame])
            rms = float(analysis["rms"][frame])
            pulse = float(analysis["pulse"][frame])
            energy = (0.35 * low + 0.25 * mid + 0.15 * rms + 0.25 * pulse) * intensity
            scale = base * (0.95 + 0.22 * energy)

            obj.scale = (scale, scale, scale)
            obj.keyframe_insert("scale", frame=frame)
            obj.location.z = 0.12 * math.sin(frame / FPS * 0.9 + index) + 0.08 * energy
            obj.keyframe_insert("location", frame=frame)
            animate_sculpture(obj, frame / FPS, energy, frame)
            emission.default_value = base * (0.025 + 0.09 * energy + 0.04 * high * intensity)
            emission.keyframe_insert("default_value", frame=frame)

            if frame % 4 == 0 or frame == end:
                obj.rotation_euler = (
                    0.4 + 0.12 * math.sin(frame / FPS * 0.35) + mid * intensity * 0.08,
                    local / FPS * 0.12 * intensity,
                    local / FPS * 0.16 * (0.5 + intensity),
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

    camera_data.lens = 43
    camera_data.dof.use_dof = True
    camera_data.dof.focus_object = target
    camera_data.dof.aperture_fstop = 4.0
    camera_data.dof.aperture_blades = 7
    # Integrate camera velocity so a new section never resets its position.
    theta, radius, velocity = -0.7, 11.5, 0.0
    for frame in range(frames):
        section_index = min(len(sections) - 1, frame * len(sections) // frames)
        behavior = sections[section_index]["visual"].get("camera_behavior", "slow_orbit")
        desired = 0.035 if behavior == "slow_orbit" else 0.0
        velocity += (desired - velocity) * 0.035
        theta += velocity / FPS
        desired_radius = 10.6 if behavior == "zoom_in" else 11.5
        radius += (desired_radius - radius) * 0.008
        shake = (0.025 * float(analysis["pulse"][frame])) if behavior == "shake" else 0
        camera.location = (radius * math.cos(theta), radius * math.sin(theta),
                           2.6 + shake * math.sin(frame * 1.7))
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
    if hasattr(scene, "eevee") and hasattr(scene.eevee, "use_raytracing"):
        scene.eevee.use_raytracing = True
    if hasattr(scene.render, "use_motion_blur"):
        scene.render.use_motion_blur = True
        scene.render.motion_blur_shutter = 0.3

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

    if not 1 <= args.resolution_percentage <= 100:
        raise SystemExit("--resolution-percentage must be between 1 and 100")
    if args.preview_seconds is not None and (not math.isfinite(args.preview_seconds) or args.preview_seconds <= 0):
        raise SystemExit("--preview-seconds must be positive and finite")

    with args.lyrics_json.open(encoding="utf-8") as handle:
        data = json.load(handle)
    analysis = load_audio(args.analysis_wav, FPS)
    if args.still_frame is not None and not 0 <= args.still_frame < analysis["frames"]:
        raise SystemExit("--still-frame must be within the audio timeline")
    if args.still_frame is not None and args.output.suffix.lower() != ".png":
        raise SystemExit("--still-frame requires a .png output")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Never let a stale video from an earlier run masquerade as this render.
    args.output.unlink(missing_ok=True)

    build_scene(data, analysis, args.samples)
    scene = bpy.context.scene
    scene.render.resolution_percentage = args.resolution_percentage
    # Preserve full-song section timing when previewing just the opening.
    if args.preview_seconds is not None:
        scene.frame_end = min(scene.frame_end, max(0, math.ceil(args.preview_seconds * FPS) - 1))
    scene.render.filepath = str(args.output.resolve())

    started_at = time.time()
    if args.still_frame is not None:
        if hasattr(scene.render.image_settings, "media_type"):
            scene.render.image_settings.media_type = "IMAGE"
        scene.render.image_settings.file_format = "PNG"
        scene.frame_set(args.still_frame)
        bpy.ops.render.render(write_still=True)
    else:
        bpy.ops.render.render(animation=True)
    resolve_rendered_output(args.output, started_at)
    print(f"OK render -> {args.output}")


if __name__ == "__main__":
    main()
