#!/usr/bin/env python3
"""Precompute the fictional team portrait with the same pinned Avatar Lab model.

Development-only dependency: numpy, Pillow and CPU onnxruntime. Model weights
stay outside Git. The deployed team page receives only this small image atlas.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image
import onnxruntime as ort

# Asset generation is local inference only; disable the runtime's incidental
# telemetry before constructing either model session.
ort.disable_telemetry_events()

ROOT = Path(__file__).resolve().parents[1]
PRESETS = [
    {"name": "neutral", "smile": .06, "pitch": 0, "roll": 0},
    {"name": "warm", "smile": .55, "pitch": 0, "roll": .3},
    {"name": "thoughtful", "smile": .03, "pitch": 1.7, "roll": -.7},
    {"name": "concerned", "smile": 0, "pitch": -1, "roll": .4},
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "services/ax/team-avatar-assets")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "services/ax/portrait-assets.json").read_text())
    files = manifest["assets"]
    paths = {}
    for name, fallback in (("appearance", "appearance.onnx"), ("distilled", "fused.onnx")):
        asset = next(value for value in files if value["path"] == "models/" + name + ".onnx")
        path = args.models / (name + ".onnx")
        if not path.exists(): path = args.models / fallback
        if digest(path) != asset["sha256"]: raise ValueError("Model checksum does not match pinned assets")
        paths[name] = path
    options = ort.SessionOptions(); options.intra_op_num_threads = 4; options.inter_op_num_threads = 1
    encoder = ort.InferenceSession(str(paths["appearance"]), options, providers=["CPUExecutionProvider"])
    model = ort.InferenceSession(str(paths["distilled"]), options, providers=["CPUExecutionProvider"])
    source = ROOT / "services/ax/avatar-assets/sample-portrait.png"
    with Image.open(source) as original:
        side = min(original.size); left = (original.width - side) // 2; top = (original.height - side) // 2
        portrait = original.convert("RGB").crop((left, top, left + side, top + side)).resize((256, 256), Image.Resampling.LANCZOS)
    x = np.asarray(portrait, dtype=np.float32).transpose(2, 0, 1)[None] / 255.
    feature = encoder.run(None, {"img": x})[0]
    atlas = Image.new("RGB", (768, 4096))
    frames = []
    for preset_index, preset in enumerate(PRESETS):
        for state in range(4):
            mouth = [0, .45, .9, 0][state]
            blink = .25 if state == 3 else 0
            for column, yaw in enumerate([-3, 0, 3]):
                expression = np.zeros((1, 30), np.float32)
                for index, value in {25: .4375, 28: -.2875, 23: .1125, 16: -.0625}.items():
                    expression[0, index] += value * mouth
                for index, value in {13: 1, 14: -.7, 18: 1, 20: -.7}.items():
                    expression[0, index] += value * blink
                for index, value in {16: -.260475, 23: -.1134375, 13: .0364, 14: -.0105,
                                     18: .0364, 20: -.0105, 4: -.0306}.items():
                    expression[0, index] += value * preset["smile"]
                pose = np.array([[yaw / 20, preset["pitch"] / 14, preset["roll"] / 14]], np.float32)
                result = model.run(None, {"feature_3d": feature, "pose_control_vector": pose,
                                         "input_rgb": x, "expression_control_vector": expression})[0]
                if not np.isfinite(result).all(): raise ValueError("Nonfinite neural frame")
                frame = Image.fromarray(np.clip(result[0].transpose(1, 2, 0) * 255, 0, 255).astype(np.uint8))
                row = preset_index * 4 + state
                atlas.paste(frame, (column * 256, row * 256))
                frames.append({"expression": preset["name"], "mouth": mouth, "blink": state == 3,
                               "yaw": yaw, "column": column, "row": row})
        print("Rendered " + preset["name"], flush=True)
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output / "portrait-atlas.webp"
    atlas.save(output, format="WEBP", quality=88, method=6)
    (args.output / "manifest.json").write_text(json.dumps({
        "mode": "precomputed_neural_portrait", "source": "bundled fictional sample",
        "source_sha256": digest(source), "appearance_sha256": digest(paths["appearance"]),
        "distilled_sha256": digest(paths["distilled"]), "runtime": ort.__version__,
        "atlas": output.name, "atlas_sha256": digest(output), "bytes": output.stat().st_size,
        "frame_size": 256, "width": 768, "height": 4096, "frames": frames,
        "lip_sync": "audio_energy_envelope; not phoneme prediction",
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
