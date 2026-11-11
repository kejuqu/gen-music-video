# 每日抽象音乐视频自动生成系统 — 全量技术文档（最终版）

**目标环境**：Apple M4 Pro / 24 GB 统一内存 / macOS  
**目标产物**：每天自动在 `~/Desktop/music-video/YYYY-MM-DD/` 下生成 **2 条** 9:16 抽象风格音乐视频，中英各一条，全程本地、无人值守。  
**模型路由**：中文 → `qwen3:8b`；英文 → `llama3.1:8b`。  
**每日产出**：`2026-11-10/zh/` 与 `2026-11-10/en/`，各含 `lyrics.json`、`song.wav`、`abstract_video.mp4`。


## 1. 系统总览

```
┌───────────┐   lang 路由    ┌────────────────────────┐  lyrics.json  ┌───────────┐
│  launchd  │ ─────────────▶ │  Ollama                │ ────────────▶ │  YoVoice  │
│  每日定时  │  zh → qwen3:8b │  format=JSON Schema    │               │  serve    │
│  两条串行  │  en → llama3.1 │  think=False           │               └─────┬─────┘
└───────────┘                └────────────────────────┘                     │ song.wav
                                                                              ▼
                                                                     ┌─────────────────┐
                                                                     │ ffmpeg 标准化    │
                                                                     │ → analysis.wav  │
                                                                     └────────┬────────┘
                                                                              ▼
                                                                     ┌─────────────────┐
                                                                     │ Blender headless │
                                                                     │ 音频驱动渲染      │
                                                                     │ → silent_video   │
                                                                     └────────┬────────┘
                                                                              ▼
                                                                     ┌─────────────────┐
                                                                     │ ffmpeg 合流      │
                                                                     │ → abstract_video │
                                                                     └─────────────────┘
```

**为什么音频分析与合流不放在 Blender 里**：Blender 无头模式下 `bpy.ops.graph.sound_bake()` 依赖 Graph Editor 上下文，稳定性差；FFMPEG 编码音频轨又受 VSE 状态影响。把“分析”与“合流”交给 ffmpeg + numpy，Blender 只负责纯视觉渲染，每个环节职责单一、可独立重试。


## 2. 技术栈与关键决策

| 环节 | 工具 | 关键决策与原因 |
|---|---|---|
| 歌词 + 视觉指令 | Ollama `qwen3:8b` / `llama3.1:8b` | `format=JSON Schema` 在 token 级约束输出，避免概率性 JSON 失败；Qwen3 必须 `think=False` |
| 音乐生成 | YoVoice `yovoice serve` | 用独立数据目录的 CLI 服务，不依赖桌面应用常驻；`--backend mps` 走 Metal |
| 音频分析 | ffmpeg + numpy | 标准化为 16-bit PCM mono 44.1 kHz，`wave` + `numpy` 逐帧 FFT 分频，无头模式最稳 |
| 3D 渲染 | Blender 4.2+ EEVEE Next | M4 Pro Metal 后端，抽象发光/粒子场景比 Cycles 快一个数量级；`--python-exit-code 1` 防止假成功 |
| 音视频合流 | ffmpeg | `-c:v copy` 不重编码视频，`-c:a aac` 编码音频，`-shortest` 对齐时长 |
| 定时 | launchd | 重启后可补跑错过的任务，自带日志落盘；不用 cron |


## 3. 环境准备

```bash
# Ollama + 两个模型
brew install ollama
ollama serve
ollama pull qwen3:8b          # 中文
ollama pull llama3.1:8b       # 英文

# Blender 4.2+ LTS（Apple Silicon 原生）
brew install --cask blender

# ffmpeg
brew install ffmpeg

# YoVoice CLI（leemysw/yovoice Releases 的 macOS arm64 二进制）
yovoice --version
yovoice status --json
```

YoVoice 首次准备（只做一次）：

```bash
mkdir -p ~/Desktop/music-video/{scripts,_logs}

yovoice setup --backend mps \
  --data-dir ~/Desktop/music-video/.yovoice-data
yovoice models download ace-step-1.5-q8 \
  --data-dir ~/Desktop/music-video/.yovoice-data

export YOVOICE_API_TOKEN="$(openssl rand -hex 32)"
echo "$YOVOICE_API_TOKEN" > ~/Desktop/music-video/.yovoice-token
chmod 600 ~/Desktop/music-video/.yovoice-token
```

两个 8B 模型 Q4 量化后各约 5 GB，24 GB 内存下可驻留；Ollama 按需换入换出。ACE-Step q8 模型常驻内存数 GB，与 Blender 同时存在不冲突，因为歌词生成结束后 Ollama 模型会被换出。


## 4. 目录结构

```
~/Desktop/music-video/
├── scripts/
│   ├── daily_pipeline.sh
│   ├── generate_lyrics.py
│   ├── generate_music.py
│   └── render_scene.py
├── .yovoice-data/              # YoVoice 模型与数据
├── .yovoice-token              # API Token（chmod 600）
├── _logs/
│   └── 2026-11-10.log
└── 2026-11-10/
    ├── zh/
    │   ├── lyrics.json
    │   ├── song.wav
    │   ├── analysis.wav        # 中间产物，可删
    │   ├── silent_video.mp4    # 中间产物，可删
    │   └── abstract_video.mp4  # 最终产物
    └── en/
        └── ...
```

按 `YYYY-MM-DD/{zh,en}/` 分目录：当天产物在一个父目录下，清理/归档以“天”为单位；脚本里 `DAY` 一处定义，子目录只多一层 `$LANG`。


## 5. 环节一：Ollama 生成歌词与结构化视觉指令

### 5.1 JSON Schema（与语言无关）

```python
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "style": {"type": "string"},
        "lyrics": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "visual": {
                        "type": "object",
                        "properties": {
                            "primary_shape": {"type": "string",
                                "enum": ["particles","crystal","torus",
                                         "fluid_metaball","wireframe_grid"]},
                            "palette": {"type": "array",
                                "items": {"type": "string"},
                                "minItems": 2, "maxItems": 4},
                            "motion_intensity": {"type": "number",
                                "minimum": 0, "maximum": 1},
                            "camera_behavior": {"type": "string",
                                "enum": ["slow_orbit","zoom_in","shake","static"]}
                        },
                        "required": ["primary_shape","palette",
                                     "motion_intensity","camera_behavior"]
                    }
                },
                "required": ["label","start_line","end_line","visual"]
            }
        }
    },
    "required": ["title","style","lyrics","sections"]
}
```

枚举约束是可靠性关键：Blender 脚本按 `primary_shape` 分支建几何体，LLM 不可能返回脚本不认识的值。`motion_intensity` 是 0–1 标量，Blender 把它映射为音频驱动的缩放/速度幅度。`style` 字段强制英文，供 ACE-Step 使用（英文风格描述更稳），而 `lyrics` 跟随目标语言。

### 5.2 `scripts/generate_lyrics.py`（完整）

```python
#!/usr/bin/env python3
import argparse, json, os, sys, time
import ollama

SCHEMA = { ... }  # 同上

MODEL_BY_LANG = {"zh": "qwen3:8b", "en": "llama3.1:8b"}

PROMPTS = {
    "zh": """生成一首中文抽象/氛围风格歌曲的歌词，主题围绕“夜晚、流动、消失的光”。
歌词 16–24 行，带 [verse] [chorus] 段落标记。
然后为每个段落生成视觉指令：primary_shape 从枚举中选，
palette 用十六进制颜色码数组，motion_intensity 随段落情绪起伏。
style 字段写一句英文音乐风格描述，用于音频生成模型。""",
    "en": """Write abstract/ambient song lyrics in English about night, flow,
and vanishing light. 16–24 lines with [verse] [chorus] markers.
Then for each section produce visual instructions: primary_shape from the enum,
palette as hex colors, motion_intensity following the emotional arc.
The style field must be a one-line music style description for the audio model."""
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    ap.add_argument("--lang", default=os.environ.get("LYRICS_LANG", "zh"))
    args = ap.parse_args()

    lang = args.lang.lower()
    if lang not in MODEL_BY_LANG:
        sys.exit(f"unsupported lang: {lang}")
    model = MODEL_BY_LANG[lang]

    last_err = None
    for attempt in range(3):
        try:
            resp = ollama.chat(
                model=model,
                messages=[{"role": "user", "content": PROMPTS[lang]}],
                format=SCHEMA,
                think=False,                    # Qwen3 必须关闭；llama3.1 忽略此参数
                options={"temperature": 0.9 if attempt == 0 else 0.5},
            )
            data = json.loads(resp["message"]["content"])
            data["_lang"] = lang
            data["_model"] = model
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"OK {model} -> {args.output}")
            return
        except (json.JSONDecodeError, KeyError) as e:
            last_err = e
            time.sleep(2)
    sys.exit(f"lyrics generation failed after 3 attempts: {last_err}")

if __name__ == "__main__":
    main()
```

`_lang` 与 `_model` 是两个元字段，下游 YoVoice 据此决定 `language` 参数，日志据此追溯“哪天哪个模型生成”。这条贯穿线保证**歌词语言、YoVoice 发音语言、目录名**三者一致。


## 6. 环节二：YoVoice 生成音乐

### 6.1 服务启动

```bash
yovoice serve --data-dir ~/Desktop/music-video/.yovoice-data \
  --listen 127.0.0.1:8080 &
```

`daily_pipeline.sh` 会在循环外探测 `/health`，未启动则拉起并轮询 30 秒，两条视频共用同一个服务实例。

### 6.2 `scripts/generate_music.py`（完整）

```python
#!/usr/bin/env python3
import argparse, json, os, requests

ap = argparse.ArgumentParser()
ap.add_argument("--lyrics", required=True)
ap.add_argument("--output", required=True)
args = ap.parse_args()

data = json.load(open(args.lyrics, encoding="utf-8"))
lang = data.get("_lang", "zh")

TOKEN = open(os.path.expanduser(
    "~/Desktop/music-video/.yovoice-token")).read().strip()
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
BASE = "http://127.0.0.1:8080"

payload = {
    "kind": "music",
    "text": data["style"],            # style 恒为英文
    "lyrics": data["lyrics"],
    "instrumental": False,
    "language": lang,                 # zh / en，与歌词一致
    "modelId": "ace-step-1.5-q8",     # 带歌词必须 ace-step / yue2，不能选 stable-audio
}

r = requests.post(f"{BASE}/v1/generate", json=payload,
                  headers=HEADERS, timeout=1800)
r.raise_for_status()
audio_id = r.headers["X-Yovoice-Generation-ID"]

audio = requests.get(f"{BASE}/v1/audio/{audio_id}",
                     headers=HEADERS, timeout=300)
audio.raise_for_status()
with open(args.output, "wb") as f:
    f.write(audio.content)
print(f"OK wav -> {args.output}")
```

**选型说明**：Stable Audio 3 只接受英文描述且只出纯音乐，带歌词场景一律不选；ACE-Step 1.5 适合带歌词歌曲生成，YuE2 是备选。


## 7. 环节三：音频标准化（ffmpeg）

Blender 侧的 `wave` 模块只读 16-bit PCM。YoVoice 输出可能是 24-bit / float / 多声道，统一标准化：

```bash
ffmpeg -y -i song.wav -ac 1 -ar 44100 -sample_fmt s16 analysis.wav
```

mono 44.1 kHz 16-bit 足够做频段分析，`song.wav` 原始文件保留用于最终合流。


## 8. 环节四：Blender 无头渲染

### 8.1 调用方式

```bash
/Applications/Blender.app/Contents/MacOS/Blender \
  --background --factory-startup -noaudio \
  --python-exit-code 1 \
  --python ~/Desktop/music-video/scripts/render_scene.py -- \
  --lyrics-json "$OUT/lyrics.json" \
  --analysis-wav "$OUT/analysis.wav" \
  --output "$OUT/silent_video.mp4"
```

- `--background`：无窗口。
- `--factory-startup`：忽略用户启动文件，保证可复现。
- `--python-exit-code 1`：**硬性要求**。Blender 默认在 Python 异常时仍返回 0，定时任务会误判成功。
- `-noaudio`：无头环境避免音频设备报错。
- `--` 之后的参数由脚本自行解析。

### 8.2 `scripts/render_scene.py`（完整基线实现）

```python
#!/usr/bin/env python3
# 在 Blender 无头模式下运行：
#   Blender --background --factory-startup -noaudio --python-exit-code 1 \
#     --python render_scene.py -- --lyrics-json L --analysis-wav W --output O
import bpy, json, sys, os, wave
import numpy as np

# ---------- 参数 ----------
def parse_args():
    argv = sys.argv
    if "--" not in argv:
        raise SystemExit("missing -- separator")
    a = argv[argv.index("--") + 1:]
    def get(flag):
        return a[a.index(flag) + 1]
    return get("--lyrics-json"), get("--analysis-wav"), get("--output")

# ---------- 音频分析 ----------
def load_audio(path, fps):
    with wave.open(path, "rb") as w:
        sr, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    if sw != 2:
        raise SystemExit(f"analysis.wav must be 16-bit PCM, got sampwidth={sw}")
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)

    frame_count = int(len(data) / sr * fps)
    win = max(2, int(sr / fps))
    freqs = np.fft.rfftfreq(win, d=1.0 / sr)
    low_m, mid_m, high_m = freqs < 250, (freqs >= 250) & (freqs < 2000), freqs >= 2000

    rms = np.zeros(frame_count); low = np.zeros(frame_count)
    mid = np.zeros(frame_count); high = np.zeros(frame_count)
    for i in range(frame_count):
        chunk = data[i * win:(i + 1) * win]
        if len(chunk) < win:
            chunk = np.pad(chunk, (0, win - len(chunk)))
        rms[i] = np.sqrt(np.mean(chunk ** 2))
        spec = np.abs(np.fft.rfft(chunk))
        low[i] = spec[low_m].mean() if low_m.any() else 0.0
        mid[i] = spec[mid_m].mean() if mid_m.any() else 0.0
        high[i] = spec[high_m].mean() if high_m.any() else 0.0

    def norm(a):
        m = a.max()
        return a / m if m > 0 else a
    return {"fps": fps, "frames": frame_count,
            "rms": norm(rms), "low": norm(low),
            "mid": norm(mid), "high": norm(high)}

# ---------- 工具 ----------
def hex_to_rgba(h, alpha=1.0):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255,
            int(h[4:6], 16) / 255, alpha)

def emissive_mat(name, color, strength=4.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    emit = nt.nodes.new("ShaderNodeEmission")
    emit.name = "Emission"
    emit.inputs["Color"].default_value = color
    emit.inputs["Strength"].default_value = strength
    nt.links.new(emit.outputs["Emission"], out.inputs["Surface"])
    return mat

def create_shape(shape, mat, loc):
    if shape == "particles":
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=0.8, location=loc)
        obj = bpy.context.object
        m = obj.modifiers.new("Wire", "WIREFRAME"); m.thickness = 0.02
    elif shape == "crystal":
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=1.0, location=loc)
        obj = bpy.context.object
    elif shape == "torus":
        bpy.ops.mesh.primitive_torus_add(location=loc, major_radius=1.0, minor_radius=0.3)
        obj = bpy.context.object
    elif shape == "fluid_metaball":
        mb = bpy.data.metaballs.new("Meta"); mb.resolution = 0.2
        obj = bpy.data.objects.new("MetaObj", mb)
        bpy.context.collection.objects.link(obj)
        el = mb.elements.new(); el.radius = 1.2
        obj.location = loc
    elif shape == "wireframe_grid":
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=20, y_subdivisions=20,
                                        size=4.0, location=loc)
        obj = bpy.context.object
        m = obj.modifiers.new("Wire", "WIREFRAME"); m.thickness = 0.03
    else:
        raise ValueError(shape)
    obj.data.materials.append(mat)
    return obj

# ---------- 主流程 ----------
def main():
    lyrics_path, audio_path, out_path = parse_args()
    data = json.load(open(lyrics_path, encoding="utf-8"))

    # 空场景
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps = 24
    fps = scene.render.fps

    analysis = load_audio(audio_path, fps)
    frames = analysis["frames"]

    # 世界背景暗色
    world = bpy.data.worlds.new("W"); world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.004, 0.004, 0.008, 1)
    bg.inputs["Strength"].default_value = 1.0
    scene.world = world

    # 按 section 均分时间轴
    sections = data["sections"]
    n = len(sections)
    per = max(1, frames // n)
    transition = max(2, per // 8)

    objs = []
    for i, sec in enumerate(sections):
        vis = sec["visual"]
        color = hex_to_rgba(vis["palette"][0])
        mat = emissive_mat(f"M{i}", color, strength=4.0)
        obj = create_shape(vis["primary_shape"], mat, (0, 0, 0))
        start = i * per
        end = frames if i == n - 1 else (i + 1) * per
        objs.append((obj, mat, start, end, vis["motion_intensity"]))

    # 逐段关键帧：只在该段可见，scale 随音频能量脉动
    for obj, mat, start, end, intensity in objs:
        emit = mat.node_tree.nodes["Emission"].inputs["Strength"]
        for f in range(start, end + 1, 2):
            # 入场/出场渐变
            if f < start + transition:
                base = (f - start) / transition
            elif f > end - transition:
                base = (end - f) / transition
            else:
                base = 1.0
            base = max(0.0, min(1.0, base))
            energy = float(analysis["low"][min(f, frames - 1)]) * intensity
            s = base * (0.6 + 0.8 * energy)
            obj.scale = (s, s, s)
            obj.keyframe_insert("scale", frame=f)
            emit.default_value = 2.0 + 6.0 * energy
            emit.keyframe_insert("default_value", frame=f)
        # 段外隐藏
        obj.scale = (0, 0, 0)
        for f in (0, start, end, frames):
            obj.keyframe_insert("scale", frame=max(0, min(f, frames - 1)))

    # 摄像机：track-to 原点，环绕
    target = bpy.data.objects.new("Target", None)
    bpy.context.collection.objects.link(target)
    cam_data = bpy.data.cameras.new("Cam")
    cam = bpy.data.objects.new("Cam", cam_data)
    bpy.context.collection.objects.link(cam)
    scene.camera = cam
    con = cam.constraints.new("TRACK_TO")
    con.target = target
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"
    R, H = 6.0, 2.5
    for f in range(0, frames, 4):
        theta = (f / frames) * 2 * 3.14159
        cam.location = (R * np.cos(theta), R * np.sin(theta), H)
        cam.keyframe_insert("location", frame=f)

    # 渲染设置
    scene.render.resolution_x, scene.render.resolution_y = 1080, 1920
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.eevee.taa_render_samples = 64
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "HIGH"
    scene.render.filepath = out_path
    scene.frame_start, scene.frame_end = 0, frames - 1

    bpy.ops.render.render(animation=True)

    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise SystemExit("render produced empty file")
    print(f"OK silent video -> {out_path}")

if __name__ == "__main__":
    main()
```

### 8.3 设计说明

- **音频分析在 Python 内完成**，不依赖 `bpy.ops.graph.sound_bake()` 的上下文。`low / mid / high` 三个频段归一化后每帧取值，直接驱动 scale 与 emission strength。
- **每个 section 一个物体**，在各自时间区间内可见，其余时间 scale=0。时间轴按 section 数量均分——歌词行数到时间不是线性的，均分是可控的近似；如需精确对齐，可在 `lyrics.json` 增加 `duration_hint`。
- **`motion_intensity` 作为乘数**调制音频能量，情绪强的段落脉动更大。
- **EEVEE Next 在 4.2 移除了 bloom**。发光感由 Emission 材质本身提供；如需光晕，在合成器加 Glare 节点（见 §14 扩展）。
- **粒子/网格基线用 ico sphere + wireframe 近似**，生产环境建议换成 Geometry Nodes 的 `Mesh to Points` + `Instance on Points`，性能更好、形态更抽象。
- **摄像机用 Track To 约束**看向原点，位置每 4 帧插一次关键帧，避免关键帧爆炸。
- **末尾校验输出文件存在且非空**，否则非零退出——这是防止“Blender 返回 0 但文件损坏”的最后一道闸。


## 9. 环节五：音视频合流（ffmpeg）

```bash
ffmpeg -y -i silent_video.mp4 -i song.wav \
  -c:v copy -c:a aac -b:a 192k -shortest \
  abstract_video.mp4
```

`-c:v copy` 不重编码视频（快且无损），`-c:a aac` 编码音频，`-shortest` 以较短流为准对齐时长。如果 `silent_video.mp4` 时长与 `song.wav` 有偏差，`-shortest` 保证不出现尾部静音或黑屏。


## 10. 编排与定时

### 10.1 `scripts/daily_pipeline.sh`（完整）

```bash
#!/bin/bash
# 注意：不能用 set -e —— 一条失败必须继续跑另一条
set -uo pipefail

DATE="$(date +%Y-%m-%d)"
ROOT="$HOME/Desktop/music-video"
DAY="$ROOT/$DATE"
mkdir -p "$DAY" "$ROOT/_logs"
exec >> "$ROOT/_logs/$DATE.log" 2>&1
echo "=== START $DATE ==="

BLENDER="/Applications/Blender.app/Contents/MacOS/Blender"
LANGS="${LANGS:-zh en}"          # 调试时可 LANGS=zh 只跑一条

# YoVoice 服务只探测/启动一次，两条共用
if ! curl -sf -o /dev/null "http://127.0.0.1:8080/health"; then
  echo "starting yovoice serve..."
  yovoice serve --data-dir "$ROOT/.yovoice-data" \
    --listen 127.0.0.1:8080 &
  for i in $(seq 1 30); do
    curl -sf -o /dev/null "http://127.0.0.1:8080/health" && break
    sleep 1
  done
  if ! curl -sf -o /dev/null "http://127.0.0.1:8080/health"; then
    echo "FATAL: yovoice serve not healthy after 30s"
    exit 1
  fi
fi

FAIL=0
for LANG in $LANGS; do
  OUT="$DAY/$LANG"
  mkdir -p "$OUT"
  echo "--- [$LANG] start $(date +%T) ---"

  if LYRICS_LANG="$LANG" python3 "$ROOT/scripts/generate_lyrics.py" \
        --output "$OUT/lyrics.json" \
     && python3 "$ROOT/scripts/generate_music.py" \
        --lyrics "$OUT/lyrics.json" \
        --output "$OUT/song.wav" \
     && ffmpeg -y -loglevel error -i "$OUT/song.wav" \
        -ac 1 -ar 44100 -sample_fmt s16 "$OUT/analysis.wav" \
     && "$BLENDER" \
        --background --factory-startup -noaudio \
        --python-exit-code 1 \
        --python "$ROOT/scripts/render_scene.py" -- \
        --lyrics-json "$OUT/lyrics.json" \
        --analysis-wav "$OUT/analysis.wav" \
        --output "$OUT/silent_video.mp4" \
     && ffmpeg -y -loglevel error \
        -i "$OUT/silent_video.mp4" -i "$OUT/song.wav" \
        -c:v copy -c:a aac -b:a 192k -shortest \
        "$OUT/abstract_video.mp4" \
     && [ -s "$OUT/abstract_video.mp4" ]; then
    echo "--- [$LANG] OK $(date +%T) ---"
  else
    echo "--- [$LANG] FAILED $(date +%T) ---"
    FAIL=1
  fi
done

echo "=== DONE $DATE (fail=$FAIL) ==="
exit $FAIL
```

关键点：

| 点 | 做法 | 原因 |
|---|---|---|
| `set -e` 禁用 | `set -uo pipefail` | `-e` 会让第一条失败直接退出，第二条永远不跑 |
| 失败隔离 | `FAIL` 标志 + `exit $FAIL` | 两条都跑完，launchd 仍能感知整体失败 |
| YoVoice 启动 | 循环外探测一次，30 s 轮询 | 避免第二条重复启动 |
| 成功判定 | `&&` 链 + `[ -s video.mp4 ]` | 防止 Blender/ffmpeg 返回 0 但文件损坏 |
| 音频标准化 | 每条独立 `analysis.wav` | 16-bit PCM mono，供 numpy 分析 |

### 10.2 launchd plist

`~/Library/LaunchAgents/com.local.musicvideo.daily.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.local.musicvideo.daily</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/YOURNAME/Desktop/music-video/scripts/daily_pipeline.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>9</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
    <key>WorkingDirectory</key>
    <string>/Users/YOURNAME/Desktop/music-video</string>
    <key>StandardOutPath</key>
    <string>/Users/YOURNAME/Desktop/music-video/_logs/launchd.out</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOURNAME/Desktop/music-video/_logs/launchd.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

将 `YOURNAME` 替换为实际用户名。加载与排查：

```bash
chmod 644 ~/Library/LaunchAgents/com.local.musicvideo.daily.plist
launchctl load ~/Library/LaunchAgents/com.local.musicvideo.daily.plist
launchctl list | grep musicvideo             # 查看退出码
launchctl start com.local.musicvideo.daily   # 立即手动触发
plutil -lint ~/Library/LaunchAgents/com.local.musicvideo.daily.plist
```

**launchd 不继承 shell 的 PATH**，必须显式声明，否则 `ollama` / `blender` / `yovoice` / `ffmpeg` 全部 `command not found`。plist 权限 `644`、属主为当前用户，否则被静默忽略。


## 11. 资源与时间估算（M4 Pro / 24 GB）

两条**串行**执行：

| 阶段 | 单条耗时（估） | 两条合计 |
|---|---|---|
| Ollama 生成 lyrics（8B + schema） | 30–60 s | 1–2 min |
| YoVoice ACE-Step 生成歌曲 | 3–8 min | 6–16 min |
| ffmpeg 标准化 | < 5 s | < 10 s |
| Blender EEVEE 渲染（约 3 min 素材，1080×1920，24 fps） | 4–10 min | 8–20 min |
| ffmpeg 合流 | < 10 s | < 20 s |
| **合计** | — | **约 15–40 min** |

**不要改成并行**：两个 Blender 同时跑会争 Metal GPU 与统一内存，实际总时长未必缩短，OOM 风险显著上升。串行时任一时刻只有一个 Blender 实例，EEVEE 在 24 GB 上渲染 10⁵ 量级粒子完全够用。

内存占用峰值：YoVoice 服务常驻数 GB + 单个 Blender 实例 + Ollama 模型（歌词生成结束后换出）。24 GB 足够，但不要在渲染时同时跑其他大内存任务。


## 12. 部署验证清单

1. `ollama list` 确认 `qwen3:8b` 与 `llama3.1:8b` 都在。
2. `yovoice models list --data-dir ~/Desktop/music-video/.yovoice-data` 确认 `ace-step-1.5-q8` 在。
3. `ffmpeg -version`、`/Applications/Blender.app/Contents/MacOS/Blender --version` 正常。
4. 手动跑 `LANGS=zh bash scripts/daily_pipeline.sh`，确认 `2026-xx-xx/zh/` 下 `lyrics.json`、`song.wav`、`abstract_video.mp4` 齐全。
5. 手动跑 `LANGS=en bash scripts/daily_pipeline.sh`，确认 `en/` 同理，且歌词确为英文。
6. 打开两个 `abstract_video.mp4` 抽查：音频有声、画面随节奏变化、9:16 比例正确、语言与目录名一致。
7. `launchctl start com.local.musicvideo.daily`，等 40 min 后检查 `_logs/` 与两个子目录。
8. 检查 `_logs/YYYY-MM-DD.log` 中每条都有 `--- [zh] OK ---` / `--- [en] OK ---`。

首次部署按 **Ollama → YoVoice → ffmpeg 标准化 → Blender → ffmpeg 合流** 顺序手动各跑一次，中英各验证一次，再交给 launchd。


## 13. 风险与应对

| 风险 | 后果 | 应对 |
|---|---|---|
| Qwen3 未关 think | 生成变慢、content 异常 | `think=False`，脚本已内置 |
| 小模型偶发不守 schema | JSON 解析失败 | `format=SCHEMA` 语法约束 + 3 次重试，重试时 temperature 降至 0.5 |
| 模型未 pull | 首次生成 404 | 部署时 `ollama list` / `yovoice models list` 预检 |
| 歌词语言与 YoVoice `language` 不一致 | 中文词配英文发音 | `lyrics.json` 写入 `_lang`，YoVoice 严格读取 |
| `style` 为中文时 ACE-Step 风格漂移 | 音乐风格不符 | prompt 强制 `style` 输出英文 |
| `set -e` 未去掉 | 第二条永远不跑 | 脚本头部显式 `set -uo pipefail`，注释说明 |
| 第一条失败仍跑第二条但状态丢失 | 不知道哪天少一条 | 循环内 `FAIL` 标志 + 日志 `FAILED` 行 |
| Blender 返回 0 但 MP4 损坏 | 假成功 | `--python-exit-code 1` + `[ -s video.mp4 ]` + 脚本末尾 `os.path.getsize` 校验 |
| YoVoice 服务中途崩溃 | 第二条音频失败 | 循环外健康检查；担心长时崩溃可在循环内再加一次 `curl` 探测并重启 |
| launchd PATH 为空 | 命令找不到 | plist 显式设 PATH |
| 24 GB 内存粒子过多 | 渲染 OOM | 固定 1080×1920，粒子 ≤10⁵，必要时改 Geometry Nodes 实例化 |
| 误改并行 | GPU/内存争抢、OOM | 文档明确串行；如需并行须把 Blender 换 CPU 或 `-t` 限线程 |
| `LANGS` 拼写错误 | 循环不执行、静默成功 | `exit $FAIL` 前可加 `[ -n "$(ls -A "$DAY")" ]` 断言；部署时手动验证一次 |

核心原则：无人值守系统的可靠性不来自“一次跑通”，而来自**每一步都有显式检查、每次失败都有日志可查**。


## 14. 可选扩展

### 14.1 两条不同主题而非中英

把循环体改为对主题列表迭代，模型仍按语言路由：

```bash
THEMES=("夜晚、流动、消失的光" "钢铁、机械、冷峻的秩序")
i=0
for THEME in "${THEMES[@]}"; do
  i=$((i+1))
  OUT="$DAY/theme$i"
  LYRICS_LANG=zh THEME="$THEME" python3 scripts/generate_lyrics.py --output "$OUT/lyrics.json"
  # 后续相同
done
```

`generate_lyrics.py` 的 prompt 中把主题句替换为 `os.environ.get("THEME", 默认主题)` 即可。

### 14.2 失败通知

在 `FAIL=1` 分支加：

```bash
osascript -e "display notification \"$DATE $LANG 生成失败\" with title \"music-video\""
```

launchd 上下文中 `osascript` 需要用户已登录 GUI，纯后台会静默失败，但不影响主流程。

### 14.3 Bloom / 光晕

Blender 4.2 EEVEE Next 移除了 bloom。在合成器加 Glare 节点：

```python
scene.use_nodes = True
nt = scene.node_tree
glare = nt.nodes.new("CompositorNodeGlare")
glare.glare_type = "FOG_GLOW"
glare.quality = "HIGH"
# 连接 Render Layers -> Glare -> Composite
```

### 14.4 更抽象的粒子

把 `particles` 分支替换为 Geometry Nodes：`Mesh to Points` → `Instance on Points`（实例为小 icosphere），音频能量驱动 `Instance Scale`。性能与形态都优于 ico sphere + wireframe 基线。

### 14.5 清理中间产物

`analysis.wav` 与 `silent_video.mp4` 可删。在 `daily_pipeline.sh` 末尾加：

```bash
find "$ROOT" -name analysis.wav -mtime +3 -delete
find "$ROOT" -name silent_video.mp4 -mtime +3 -delete
```

保留 3 天便于排查，之后自动清理。