# 每日音乐视频自动生成系统

一个面向 macOS / Apple Silicon 的本地音乐视频流水线：每天按 **zh → en 排队**生成中文、英文两条 **3 分钟**歌曲，并为每条歌曲输出 **16:9 横版**和 **9:16 竖版**两个带卡拉 OK 歌词的音乐视频（外加 MP3），可通过 `launchd` 定时无人值守运行。

系统使用 Ollama 生成歌词和结构化视觉指令，使用 YoVoice / ACE-Step 生成歌曲，视频由项目自研的 Python 渲染器（`render_visualizer.py`）逐帧合成：旋转黑胶唱片 + 彩虹环形频谱 + 逐词时间轴对齐的底部歌词，最后由 ffmpeg 混入音轨。默认创作方向为**阳光、希望、成长、幸福与欢乐**：歌词和音乐风格必须积极向上、幸福欢乐，视觉默认明亮温暖。

> **验证边界**：本 README 提供完整部署与验证步骤，不代表当前环境已经实际完成模型下载或歌曲生成。首次部署必须按[首次手动验证](#首次手动验证)逐项检查，确认中英两条链路均可用后再启用定时任务。

## 三分钟音乐与节奏视觉

歌曲默认请求 **180 秒**（3 分钟），使用 YoVoice 的 `modelOptions.ace_step.duration_seconds=180`。可用环境变量 `MUSIC_DURATION` 或 `generate_music.py --duration` 覆盖（例如调试时设 `MUSIC_DURATION=30`）。生成后通过 ffprobe 检查实际时长：偏差超过 1 秒即报错；仅修正 1 秒以内的模型取整误差，输出精确时长的 PCM WAV，并添加自然淡出。不会循环短歌曲凑时长。运行环境需要 ffmpeg 和 ffprobe。

视频画面由真实音频逐帧驱动：128 根 3px 彩虹频谱条（色相按角度排布并随时间缓慢漂移）围绕中央黑胶唱片（封面图作唱片标签，按真实唱机 **33⅓ RPM** 旋转），条高经对数增益映射频谱能量，背后是模糊封面背景、能量辉光和暗角。歌词由 **mlx-whisper 单词级时间戳**与歌词文本做字符级全局对齐得到逐句时间轴，随演唱在底部显示（中文按字、英文按词自动换行，最多两行；纯器乐间奏自动清屏；单句最长显示 15 秒）。

修改生成逻辑不会改变已有产物，需要重新运行流水线生成新作品。

## 功能特性

- **每日双语排队产出**：默认按 `zh`、`en` 顺序串行执行（单条失败不阻塞另一条），中文路由到 `qwen3:8b`，英文路由到 `llama3.1:8b`。
- **双比例成片**：每首歌曲同时输出 1280×720（16:9）和 720×1280（9:16，适配 TikTok / Reels / Shorts）两个 MP4，外加同名 MP3。
- **积极快乐的结构化创作**：Ollama 使用 JSON Schema 约束标题、英文风格描述、歌词及每段视觉参数；默认歌词与音乐风格积极向上、幸福欢乐，失败时自动重试。
- **本地音乐生成**：通过 YoVoice HTTP 服务调用 ACE-Step 模型，歌词语言与目录语言保持一致。
- **卡拉 OK 歌词**：本地 mlx-whisper（Apple Silicon 优化，模型约 1.5 GB，首次转写自动下载）生成单词级时间戳，与歌词做字符级对齐；幻觉段落自动跳过，稀疏转写自动回退整段匹配。
- **音频驱动画面**：visgen 的 `AudioProcessor` 做逐帧 FFT 频谱分析；对数增益映射让弱频段也有细节，能量驱动辉光呼吸。
- **可靠的无人值守编排**：单条失败不会阻止另一条执行；全部结束后以非零退出码汇总失败。
- **便于排查**：按日期保存日志和中间产物，可单独重跑任一语言，也可配置中间文件保留天数。

## 工作流程

```text
launchd / 手动执行
        │
        ▼
Ollama（zh → qwen3:8b，en → llama3.1:8b）
        │  lyrics.json：歌词 + style + sections 视觉指令（palette 用于封面与配色）
        ▼
YoVoice serve（ACE-Step）
        │  song.wav（默认 180 秒）
        ▼
ffmpeg 导出 MP3
        │
        ▼
render_visualizer.py（每个语言跑两次）
  ├─ mlx-whisper 歌词对齐 → timing.json（首次渲染时生成并缓存）
  ├─ 程序化生成封面 → cover.png
  ├─ 逐帧合成：黑胶唱片 + 彩虹频谱 + 底部歌词（无声）
  └─ h264 编码（优先 videotoolbox 硬件编码）
        │  silent_16x9.mp4 / silent_9x16.mp4
        ▼
ffmpeg 合流（视频流直接复制 + AAC 192k 音轨）
        │
        ▼
<安全化的歌词标题>_YYYY-MM-DD.mp4      ← 16:9
<安全化的歌词标题>_YYYY-MM-DD_9x16.mp4 ← 9:16
<安全化的歌词标题>_YYYY-MM-DD.mp3
```

每个阶段都可独立执行和重试。同一语言内 16:9 与 9:16 共用 `timing.json` 和 `cover.png`，只渲染画面两遍。

## 目录结构

建议将项目完整放在 `~/Desktop/music-video`：

```text
~/Desktop/music-video/
├── README.md
├── pyproject.toml               # uv 项目、运行依赖与开发依赖声明
├── .python-version              # 固定项目 Python 为 3.12
├── uv.lock                      # uv 锁文件（若生成成功，应随项目保留）
├── requirements.txt             # 非 uv 用户的运行依赖兼容文件
├── requirements-dev.txt         # 非 uv 用户的测试依赖兼容文件
├── config/
│   └── pipeline.env.example     # 流水线配置模板
├── examples/
│   └── lyrics.zh.json           # 歌词与视觉指令示例
├── scripts/
│   ├── .env                     # 本地配置，由模板复制（自行创建）
│   ├── daily_pipeline.sh        # 每日串行编排、服务探测、日志与清理
│   ├── generate_lyrics.py       # Ollama 歌词及视觉指令生成
│   ├── generate_music.py        # YoVoice 歌曲生成与音频下载（--duration 可改时长）
│   ├── lyric_timing.py          # mlx-whisper 歌词时间轴（单词级对齐）
│   ├── render_visualizer.py     # 视频渲染：黑胶 + 彩虹频谱 + 歌词（--aspect 16:9/9:16）
│   ├── video_filename.py        # 最终文件名安全化
│   ├── install_launchd.sh       # 渲染并加载 launchd 配置
│   ├── audio_analysis.py        # 【旧流程】PCM 音频读取及逐帧 FFT 分析
│   ├── cinematic_visuals.py     # 【旧流程】Blender 场景模块，当前管线不再调用
│   ├── render_scene.py          # 【旧流程】Blender 无头渲染，当前管线不再调用
│   └── render_audioglow.py      # 【旧流程】audioglow 渲染，当前管线不再调用
├── launchd/
│   └── com.local.musicvideo.daily.plist.template
├── tests/                       # 离线单元测试
├── .venv/                       # uv sync --dev 创建的虚拟环境
├── .yovoice-data/               # YoVoice 模型与数据（自行生成）
├── .yovoice-token               # API Token，权限必须为 600
├── _logs/
│   ├── YYYY-MM-DD.log           # 每日流水线日志
│   ├── launchd.out              # launchd 标准输出
│   └── launchd.err              # launchd 标准错误
└── YYYY-MM-DD/
    ├── zh/
    │   ├── lyrics.json
    │   ├── song.wav
    │   ├── timing.json          # 每句歌词的起止时间（渲染时缓存，可删了重算）
    │   ├── cover.png            # 程序化生成的专辑封面
    │   ├── silent_16x9.mp4      # 中间产物（无声）
    │   ├── silent_9x16.mp4      # 中间产物（无声）
    │   ├── 向阳而生_2026-11-10.mp4        # 最终视频：安全化标题_日期（16:9，带音轨）
    │   ├── 向阳而生_2026-11-10_9x16.mp4   # 最终视频（9:16，带音轨）
    │   └── 向阳而生_2026-11-10.mp3        # 最终音频
    └── en/
        └── ...
```

最终视频命名为 `<安全化的歌词title>_YYYY-MM-DD.mp4`（竖版追加 `_9x16`）。标题中的中文会保留，空白转为下划线，非法文件名字符替换为下划线。例如：

```text
~/Desktop/music-video/2026-11-10/zh/向阳而生_2026-11-10.mp4
~/Desktop/music-video/2026-11-10/zh/向阳而生_2026-11-10_9x16.mp4
~/Desktop/music-video/2026-11-10/en/Sunshine_and_Joy_2026-11-10.mp4
~/Desktop/music-video/2026-11-10/en/Sunshine_and_Joy_2026-11-10_9x16.mp4
```

`YYYY-MM-DD` 是本次任务的 `DATE`，不是文件修改时间。

## 依赖已准备好时的快速启动

以下命令假定已执行 `uv sync --dev`，已配置 `scripts/.env`，Ollama 和 YoVoice 均可用：

```bash
cd "$HOME/Desktop/music-video"
LANGS='zh en' bash scripts/daily_pipeline.sh
```

运行时可在另一个终端跟踪日志：

```bash
tail -f "$HOME/Desktop/music-video/_logs/$(date +%F).log"
```

两种语言均显示 `OK` 后，打开当天全部成片：

```bash
DATE_STAMP="$(date +%F)"
DAY="$HOME/Desktop/music-video/$DATE_STAMP"
find "$DAY/zh" "$DAY/en" -maxdepth 1 -type f \
  \( -name "*_${DATE_STAMP}.mp4" -o -name "*_${DATE_STAMP}_9x16.mp4" -o -name "*_${DATE_STAMP}.mp3" \) \
  -exec open {} +
```

如果使用了自定义 `MUSIC_VIDEO_ROOT` 或 `DATE`，请将上述路径替换为对应目录。快速启动只是运行入口，不代表真实生成已经验证成功。

## 环境要求

- macOS，推荐 Apple Silicon；需求目标为 M4 Pro / 24 GB 统一内存。
- **uv 0.11.12**，作为主依赖管理工具；项目使用 **Python 3.12**，`.python-version` 已固定为 `3.12`，`pyproject.toml` 要求至少 Python 3.10。
- Ollama，以及 `qwen3:8b`、`llama3.1:8b` 两个模型。
- 可访问的 YoVoice 服务和 ACE-Step 模型；仅首次准备模型或需要脚本自动拉起服务时才需要 YoVoice macOS arm64 CLI。
- ffmpeg（含 ffprobe）。视频渲染不需要 Blender、不需要 GPU OpenGL 环境。

> 8B 语言模型、ACE-Step 和 mlx-whisper 都会占用较多统一内存。生成期间不要同时运行其他大型模型或高内存任务。mlx-whisper 的模型文件（约 1.5 GB）在首次歌词对齐时自动下载到 Hugging Face 缓存。

## 安装

### 1. 放置项目

将本项目全部文件复制到默认目录：

```bash
mkdir -p "$HOME/Desktop/music-video"
# 将 README.md、pyproject.toml、scripts、config、examples、launchd、tests 等复制到该目录
# 如果已经生成 uv.lock，也请一并复制
cd "$HOME/Desktop/music-video"
```

如果需要使用其他位置，请设置 `MUSIC_VIDEO_ROOT`，并确保启动脚本、YoVoice 数据目录、令牌及输出目录使用同一套路径配置。

### 2. 使用 uv 安装依赖

安装 uv 并检查版本：

```bash
brew install uv
uv --version  # 本文以 uv 0.11.12 为准
```

准备 Python 3.12 并同步项目依赖：

```bash
cd "$HOME/Desktop/music-video"
uv python install 3.12   # 已安装时会跳过
uv sync --dev            # 读取 .python-version，自动创建 .venv

uv python find 3.12
.venv/bin/python3 --version
uv run --no-sync python -c 'import sys, ollama, requests, numpy, visgen, mlx_whisper; print(sys.version); print("dependencies OK")'
```

预期项目解释器为 **CPython 3.12.x**。不要使用系统 `/usr/bin/python3` 的 3.9 运行本项目。

`uv sync --dev` 根据 `pyproject.toml` 创建 `.venv`，并同时安装运行和测试依赖（含 `visgen`、`mlx-whisper`，渲染器还需要 Pillow / NumPy / OpenCV，均由依赖自动带入）。`requirements.txt` 和 `requirements-dev.txt` 只为非 uv 用户保留兼容路径，不是本项目的推荐主路径。

非 uv 用户可自行创建虚拟环境，并使用 pip 安装 `requirements-dev.txt`（其中包含运行依赖）；只运行生产流水线而不跑测试时也可安装 `requirements.txt`。

赋予脚本执行权限：

```bash
chmod +x scripts/daily_pipeline.sh scripts/install_launchd.sh
```

每日 shell 流水线不会通过 `uv run` 包装每个脚本，因此仍需按[配置](#配置)把 `scripts/.env` 的 `PYTHON` 设为项目 `.venv/bin/python3` 的绝对路径。

### 3. 安装并准备 Ollama

```bash
brew install ollama
ollama serve
```

保持 `ollama serve` 运行，再打开另一个终端下载模型：

```bash
ollama pull qwen3:8b
ollama pull llama3.1:8b
ollama list
```

也可以使用 Homebrew 服务让 Ollama 常驻：

```bash
brew services start ollama
```

检查服务是否可访问：

```bash
curl -f http://127.0.0.1:11434/api/tags
```

### 4. 安装并准备 YoVoice

从 `leemysw/yovoice` Releases 获取 macOS arm64 二进制，将 `yovoice` 放入 `PATH` 可搜索的位置，例如 `/opt/homebrew/bin`。随后检查：

```bash
yovoice --version
yovoice status --json
```

首次初始化独立数据目录并下载歌曲模型：

```bash
export MUSIC_VIDEO_ROOT="${MUSIC_VIDEO_ROOT:-$HOME/Desktop/music-video}"
mkdir -p "$MUSIC_VIDEO_ROOT"/{_logs,.yovoice-data}

yovoice setup --backend mps \
  --data-dir "$MUSIC_VIDEO_ROOT/.yovoice-data"
yovoice models download ace-step-1.5-q8 \
  --data-dir "$MUSIC_VIDEO_ROOT/.yovoice-data"
yovoice models list --data-dir "$MUSIC_VIDEO_ROOT/.yovoice-data"
```

生成并保存 API Token：

```bash
export YOVOICE_API_TOKEN="$(openssl rand -hex 32)"
printf '%s\n' "$YOVOICE_API_TOKEN" > "$MUSIC_VIDEO_ROOT/.yovoice-token"
chmod 600 "$MUSIC_VIDEO_ROOT/.yovoice-token"
unset YOVOICE_API_TOKEN
```

不要把 `.yovoice-token` 或本地 `scripts/.env` 提交到版本库。

流水线只通过 HTTP API 生成音乐。**如果 YoVoice 服务已经健康运行，流水线不要求安装或找到 `yovoice` CLI。** 只有健康检查失败、需要脚本自动执行 `yovoice serve` 拉起服务时，才要求 CLI 位于 `PATH`，并准备好本地数据目录和模型。两种语言共用一个服务实例。

脚本会读取令牌文件并导出 `YOVOICE_API_TOKEN`，确保自动拉起的服务与客户端使用同一凭据。需要手动启动时执行：

```bash
export YOVOICE_API_TOKEN="$(cat "$MUSIC_VIDEO_ROOT/.yovoice-token")"
yovoice serve \
  --data-dir "$MUSIC_VIDEO_ROOT/.yovoice-data" \
  --listen 127.0.0.1:8080
```

### 5. 安装 ffmpeg

```bash
brew install ffmpeg
ffmpeg -version
```

视频渲染和歌词对齐都依赖 ffmpeg；不需要安装 Blender。

## 配置

`daily_pipeline.sh` 启动时会读取 `scripts/.env`。从模板创建并修改该文件：

```bash
cd "$HOME/Desktop/music-video"
cp config/pipeline.env.example scripts/.env
chmod 600 scripts/.env
```

至少将模板中的 `YOURNAME` 替换为实际用户名。`scripts/.env` 中的 Python 配置示例：

```bash
PYTHON=/Users/YOURNAME/Desktop/music-video/.venv/bin/python3
```

修改后验证解释器及依赖：

```bash
"$HOME/Desktop/music-video/.venv/bin/python3" -c 'import ollama, requests, visgen, mlx_whisper'
```

文件中的路径必须是**展开后的绝对路径**，不要使用 `~` 或 `$HOME`。完整条目以 `config/pipeline.env.example` 为准。

| 变量 | 默认/示例 | 用途 |
| --- | --- | --- |
| `MUSIC_VIDEO_ROOT` | `/Users/YOURNAME/Desktop/music-video` | 项目数据、日志和每日产物根目录 |
| `PYTHON` | `/Users/YOURNAME/Desktop/music-video/.venv/bin/python3` | 执行 Python 脚本的解释器；launchd 必须配置此项 |
| `LANGS` | `zh en` | 语言及串行顺序，只支持 `zh`、`en`；调试可设为 `zh` |
| `MUSIC_DURATION` | `180` | 歌曲时长（秒）；调试可设为 `30` |
| `THEME` | 各语言内置的“阳光、希望、成长、幸福与欢乐”主题 | 可选，覆盖歌词主题；默认内容必须积极向上、幸福欢乐 |
| `DATE` | 当天 `YYYY-MM-DD` | 可选，指定补跑日期 |
| `YOVOICE_HOST` / `YOVOICE_PORT` | `127.0.0.1` / `8080` | YoVoice 监听地址和端口 |
| `YOVOICE_API_BASE` | 由 host/port 推导 | 可选，显式覆盖完整 API 地址 |
| `YOVOICE_DATA_DIR` | `$MUSIC_VIDEO_ROOT/.yovoice-data` | YoVoice 模型与数据目录 |
| `YOVOICE_TOKEN_FILE` | `$MUSIC_VIDEO_ROOT/.yovoice-token` | Bearer Token 文件路径 |
| `YOVOICE_MODEL_ID` | 自动选择已安装的 ACE-Step 模型 | 可选，显式指定歌曲模型 |
| `RETENTION_DAYS` | 默认不清理，模板示例为 `3` | 可选，清理超过指定天数的分析 WAV 和无声视频 |
| `NOTIFY_FAILURE` | `0` | 设为 `1` 时尝试发送 macOS 失败通知 |

`scripts/.env` 提供默认值，命令前显式传入的环境变量优先级更高，例如 `LANGS=zh bash scripts/daily_pipeline.sh` 会覆盖文件中的 `LANGS`。日常调试既可修改配置文件，也可使用命令前缀临时覆盖：

```bash
THEME='阳光、友谊与一起成长的快乐' LANGS='zh' MUSIC_DURATION=30 bash scripts/daily_pipeline.sh
```

直接分步调用 Python 脚本不会自动读取此文件，需要先 `source scripts/.env` 或自行导出相关变量。

## 首次手动验证

首次部署不要直接交给 launchd。按 **Ollama → YoVoice → ffmpeg → 歌词对齐 → 渲染 → 合流** 的顺序验证，再运行完整流水线。

以下命令假定：

```bash
cd "$HOME/Desktop/music-video"
set -a
source scripts/.env
set +a
TEST_OUT="$MUSIC_VIDEO_ROOT/$(date +%F)/zh"
mkdir -p "$TEST_OUT"
```

这里显式加载 `scripts/.env`，让分步命令与每日流水线使用相同地址、令牌和模型配置。

### 1. 验证歌词与视觉 JSON

```bash
uv run python scripts/generate_lyrics.py \
  --lang zh \
  --output "$TEST_OUT/lyrics.json"
uv run python -m json.tool "$TEST_OUT/lyrics.json" >/dev/null
```

检查文件至少包含 `title`、`style`、`lyrics`、`sections`，并确认：

- `_lang` 为 `zh`，`_model` 为 `qwen3:8b`；
- 默认歌词积极向上、幸福欢乐，`style` 是表达快乐、希望与明亮氛围的英文音乐风格描述；
- 默认视觉配色明亮温暖，与阳光、希望和成长的情绪一致；
- `primary_shape` 和 `camera_behavior` 属于脚本支持的枚举；
- `palette` 为有效十六进制颜色，`motion_intensity` 在 0 到 1 之间。

英文链路使用 `--lang en` 或 `LYRICS_LANG=en`，应路由到 `llama3.1:8b`。

### 2. 验证 YoVoice 歌曲生成

先确认服务健康。流水线会先尝试 `/health`，再使用 Bearer Token 尝试 `/v1/status`：

```bash
curl -f -H "Authorization: Bearer $(cat "$MUSIC_VIDEO_ROOT/.yovoice-token")" \
  http://127.0.0.1:8080/v1/status
```

然后生成 30 秒短歌做快速验证（完整流水线默认 180 秒）：

```bash
uv run python scripts/generate_music.py \
  --lyrics "$TEST_OUT/lyrics.json" \
  --output "$TEST_OUT/song.wav" \
  --duration 30
ffprobe -v error -show_streams -show_format "$TEST_OUT/song.wav"
```

若使用非默认地址，确保 `YOVOICE_API_BASE` 或 `YOVOICE_HOST` / `YOVOICE_PORT` 与健康检查地址一致。带歌词的音乐必须使用 ACE-Step / YuE2 一类支持歌曲生成的模型，不要改用只生成纯音乐的 Stable Audio。

### 3. 验证歌词时间轴（mlx-whisper）

```bash
uv run python scripts/lyric_timing.py \
  --song "$TEST_OUT/song.wav" \
  --lyrics-json "$TEST_OUT/lyrics.json" \
  --output "$TEST_OUT/timing.json"
```

首次运行会自动下载 whisper 模型（约 1.5 GB）。检查 `timing.json`：每句 `start`/`end` 应在合理范围（单句 2–10 秒），且整体随时间递增。`[verse]` 等段落标记不会出现在字幕里。

### 4. 验证视频渲染（无声）

```bash
uv run python scripts/render_visualizer.py \
  --lyrics-json "$TEST_OUT/lyrics.json" \
  --song-wav "$TEST_OUT/song.wav" \
  --output "$TEST_OUT/silent_16x9.mp4" \
  --aspect 16:9

uv run python scripts/render_visualizer.py \
  --lyrics-json "$TEST_OUT/lyrics.json" \
  --song-wav "$TEST_OUT/song.wav" \
  --output "$TEST_OUT/silent_9x16.mp4" \
  --aspect 9:16

test -s "$TEST_OUT/silent_16x9.mp4" && test -s "$TEST_OUT/silent_9x16.mp4"
```

渲染会复用已生成的 `timing.json` 和 `cover.png`；删除它们可强制重新对齐/重新生成封面。检查画面：黑胶唱片持续旋转、彩虹频谱随音乐跳动、底部歌词与演唱同步。

### 5. 验证音视频合流

```bash
SAFE_TITLE='向阳而生'  # 替换为实际 title 的安全化结果
FINAL_VIDEO="$TEST_OUT/${SAFE_TITLE}_$(date +%F).mp4"
FINAL_VIDEO_9X16="$TEST_OUT/${SAFE_TITLE}_$(date +%F)_9x16.mp4"
ffmpeg -y -loglevel error \
  -i "$TEST_OUT/silent_16x9.mp4" -i "$TEST_OUT/song.wav" \
  -c:v copy -c:a aac -b:a 192k -shortest -movflags +faststart \
  "$FINAL_VIDEO"
ffmpeg -y -loglevel error \
  -i "$TEST_OUT/silent_9x16.mp4" -i "$TEST_OUT/song.wav" \
  -c:v copy -c:a aac -b:a 192k -shortest -movflags +faststart \
  "$FINAL_VIDEO_9X16"

test -s "$FINAL_VIDEO" && test -s "$FINAL_VIDEO_9X16"
ffprobe -v error -show_entries stream=codec_type -of csv=p=0 "$FINAL_VIDEO"
```

预期每个文件都输出 `video` 和 `audio` 两行。打开视频抽查：有声、画面随节奏变化、横竖两个比例、歌词与演唱同步，且歌曲语言与目录一致。

### 6. 验证完整编排

分别验证中文和英文，再验证默认双语言任务：

```bash
LANGS='zh' MUSIC_DURATION=30 bash scripts/daily_pipeline.sh
LANGS='en' MUSIC_DURATION=30 bash scripts/daily_pipeline.sh
bash scripts/daily_pipeline.sh
```

确认两个目录中都有 `lyrics.json`、`song.wav`、两个最终 MP4 和一个 MP3。两条任务应串行执行；若第一条失败，第二条仍应尝试，日志末尾通过 `fail=1` 和非零退出码汇总失败。

补跑指定日期：

```bash
DATE='2026-11-10' LANGS='zh en' bash scripts/daily_pipeline.sh
```

重复执行会重新生成同一日期、同一语言的阶段产物。最终视频按标题区分：同一天、同一语言生成不同标题时，旧标题的 MP4 可以保留；安全化后的标题和日期均相同时，同名 MP4 会覆盖。补跑前请备份需要保留的版本。

## launchd 定时运行

模板默认在每天本地时间 **09:00** 触发，实际以 `launchd/com.local.musicvideo.daily.plist.template` 为准。

### 安装或更新

先确认 `scripts/.env` 中的 `PYTHON` 是 `.venv/bin/python3` 的绝对路径，再执行：

```bash
cd "$HOME/Desktop/music-video"
bash scripts/install_launchd.sh
```

**无需先激活虚拟环境。** 安装脚本只用系统 `python3` 替换模板中的 `@PROJECT_ROOT@`，再创建日志目录、校验并加载 plist；它不检测虚拟环境，也不写入 Python 路径。实际解释器由每日脚本从 `scripts/.env` 的 `PYTHON` 读取。

仅修改 `scripts/.env` 不需要重装 launchd；移动项目或修改 plist 模板后才需重新运行安装脚本。

生成后的 Agent 位于：

```text
~/Library/LaunchAgents/com.local.musicvideo.daily.plist
```

检查配置和任务状态：

```bash
plutil -lint "$HOME/Library/LaunchAgents/com.local.musicvideo.daily.plist"
launchctl list | grep musicvideo
```

立即触发一次：

```bash
launchctl start com.local.musicvideo.daily
```

若当前系统不再识别旧的 `launchctl start` 用法，可使用：

```bash
launchctl kickstart -k "gui/$(id -u)/com.local.musicvideo.daily"
```

触发后实时查看日志：

```bash
tail -f "$MUSIC_VIDEO_ROOT/_logs/$(date +%F).log"
```

> launchd 不继承交互式 shell 的临时变量。模板已声明常见 Homebrew/系统 `PATH`；YoVoice 若安装在其他目录，需将实际目录补入模板并重装。plist 权限应为 `644`，属主为当前用户。

### 卸载定时任务

如需停止定时生成：

```bash
launchctl bootout "gui/$(id -u)/com.local.musicvideo.daily"
rm "$HOME/Library/LaunchAgents/com.local.musicvideo.daily.plist"
```

该操作只移除定时任务，不删除项目、模型或历史产物。

## 日志、结果与清理

每日日志：

```bash
less "$MUSIC_VIDEO_ROOT/_logs/$(date +%F).log"
grep -E 'START|OK|FAILED|FATAL|DONE' \
  "$MUSIC_VIDEO_ROOT/_logs/$(date +%F).log"
```

launchd 自身日志：

```bash
tail -n 100 "$MUSIC_VIDEO_ROOT/_logs/launchd.out"
tail -n 100 "$MUSIC_VIDEO_ROOT/_logs/launchd.err"
```

每种语言应以 `OK` 结束；失败时会记录 `FAILED`，整个脚本最终以非零状态退出。`RETENTION_DAYS` 控制旧分析音频和无声视频的自动清理，原始歌曲、最终 MP4/MP3、歌词及日志不会因该设置被当作中间文件删除。清理前仍建议先备份重要产物。

## 测试

主路径使用 uv 同步运行及测试依赖，然后任选标准库 `unittest` 或 `pytest`：

```bash
cd "$HOME/Desktop/music-video"
uv sync --dev
uv run python -m unittest discover -s tests -v
# 或者：uv run pytest -q
```

`requirements-dev.txt` 仅供非 uv 用户通过 pip 安装同一组开发依赖。

单元测试覆盖歌词数据校验、音乐请求构造、时长校验、文件名安全化和歌词对齐（换行、幻觉跳过、显示上限）等离线逻辑，不会替代以下真实集成验证：

- Ollama 模型可下载并能按 Schema 返回内容；
- YoVoice 可启动、鉴权、生成并返回音频；
- mlx-whisper 首次可下载模型并完成转写；
- 渲染器可调用 ffmpeg 编码，合流后的 MP4 有声音且音视频同步。

## 常见问题

### `ollama` / `yovoice` / `ffmpeg`：`command not found`

交互终端正常而 launchd 失败，通常是 plist 的 `PATH` 不完整。将 `command -v <命令>` 的实际目录加入模板，再重新运行安装脚本。Apple Silicon Homebrew 常见前缀为 `/opt/homebrew/bin`。若 YoVoice 服务已健康运行，则无需为了解决 `yovoice: command not found` 安装 CLI；只有需要脚本自动拉起服务时才必须配置该命令。

### Ollama 连接失败或模型返回 404

```bash
curl -f http://127.0.0.1:11434/api/tags
ollama list
ollama pull qwen3:8b
ollama pull llama3.1:8b
```

确认 Ollama 在 launchd 运行之前已经启动。中文模型必须关闭 thinking 模式；项目请求中使用 `think=False`，不要为追求推理输出而重新开启。

### 歌词 JSON 解析失败或字段不符合预期

小模型偶发不遵守结构。流水线使用 JSON Schema 约束并重试，重试时降低 temperature。若持续失败，保留报错日志，检查模型版本、主题提示词和返回内容，不要直接跳过 Schema 校验。

### YoVoice 健康检查、401 或模型错误

```bash
curl -v -H "Authorization: Bearer $(cat "$MUSIC_VIDEO_ROOT/.yovoice-token")" \
  http://127.0.0.1:8080/v1/status
ls -l "$MUSIC_VIDEO_ROOT/.yovoice-token"
yovoice models list --data-dir "$MUSIC_VIDEO_ROOT/.yovoice-data"
```

- 健康检查失败：确认端口、服务进程和 `--data-dir`。
- 401/403：确认 Token 文件非空、权限为 `600`，且与服务配置使用同一 Token。
- 模型不存在：重新下载 ACE-Step 模型。
- 中文发音异常：检查 `lyrics.json` 的 `_lang`，不要手动让歌词语言与 `language` 参数不一致。

### 歌词对不上或某句停留太久

`timing.json` 是渲染时缓存的。修正对齐逻辑或换歌后，删除对应语言目录下的 `timing.json` 再重跑，强制重新转写对齐。若某句长期识别不到（哼唱、含糊），该句会按前一句结束时间插入；整段缺失时回退整段匹配。单句显示最长 15 秒，之后自动清屏等待下一句。

### 渲染报错或 MP4 为空

渲染器要求 ffmpeg 在 `PATH` 中（优先使用 `h264_videotoolbox` 硬件编码，缺失时回退 `libx264`）。查看当日日志中渲染阶段的 Python traceback；不仅要看进程返回码，还要确认 `test -s silent_16x9.mp4` 非空。不要在渲染的同时跑其他大内存任务。

### 视频没有声音

渲染阶段产出的是无声视频（`silent_*.mp4`），最终音轨由合流阶段添加。若最终 MP4 没有音轨，检查合流阶段日志，并手动验证：

```bash
ffprobe -v error -show_entries stream=codec_type -of csv=p=0 <最终视频>
```

预期输出 `video` 和 `audio` 两行。

### 视频时长或声音异常

最终合流使用 `-shortest`，视频与音频以较短者为准。若画面提前结束，先检查无声视频和歌曲时长：

```bash
# 在待检查的 zh/ 或 en/ 产物目录中执行
for file in song.wav silent_16x9.mp4 silent_9x16.mp4 ./*_????-??-??.mp4; do
  [ -f "$file" ] || continue
  printf '%s: ' "$file"
  ffprobe -v error -show_entries format=duration \
    -of default=noprint_wrappers=1:nokey=1 "$file"
done
```

### 内存不足、生成极慢或机器卡顿

不要并行运行两条流水线。关闭其他大模型、渲染任务和高内存应用；保留一个 YoVoice 服务实例。歌词对齐（mlx-whisper）和渲染（逐帧合成）会竞争统一内存，但渲染本身已使用硬件编码，3 分钟双比例成片通常在 1–2 分钟内完成。

## 资源与运行时间参考

在 M4 Pro / 24 GB 的目标环境中，需求估算如下，实际时间随歌曲长度、模型、系统负载变化：

| 阶段 | 单语言估算 |
| --- | ---: |
| Ollama 歌词与视觉 JSON | 30–60 秒 |
| YoVoice ACE-Step 歌曲（180 秒） | 3–8 分钟 |
| ffmpeg 导出 MP3 | 少于 5 秒 |
| mlx-whisper 歌词对齐（首次含模型下载） | 10–60 秒（下载约 1–3 分钟） |
| 渲染 16:9 + 9:16（硬件编码） | 1–2 分钟 |
| ffmpeg 合流 × 2 | 少于 20 秒 |
| **单语言总计** | **约 5–12 分钟** |
| **双语合计（串行）** | **约 10–25 分钟** |

这些数字不是性能承诺。两条语言任务必须串行执行，避免统一内存争用。视频输出为 1280×720（16:9）和 720×1280（9:16）、30 fps、h264；不再需要 Blender 渲染阶段。

## 部署验收清单

- [ ] `ollama list` 中同时存在 `qwen3:8b` 和 `llama3.1:8b`。
- [ ] YoVoice 健康检查通过，ACE-Step 模型已下载，Token 文件权限为 `600`。
- [ ] ffmpeg 版本命令正常，`PYTHON` 指向项目 `.venv`，`visgen` / `mlx_whisper` 可导入。
- [ ] 中文手动流水线成功，英文手动流水线成功。
- [ ] 每种语言的 16:9 和 9:16 最终 MP4 均非空、有声音、画面随节奏变化、歌词与演唱同步，比例正确。
- [ ] `lyrics.json` 的语言、模型元字段、YoVoice 发音语言和输出目录一致。
- [ ] 已执行 `uv sync --dev`，运行 `uv run python -m unittest discover -s tests -v` 或 `uv run pytest -q`，并处理所有失败。
- [ ] plist 通过 `plutil -lint`，launchd 中能看到任务。
- [ ] 手动触发 launchd 后，日志中中文和英文均以 `OK` 结束。
- [ ] 已确认定时任务的 `PATH`、Python 绝对路径、根目录和 Token 路径不依赖当前终端。

完成以上检查后，再将系统视为可无人值守运行。

