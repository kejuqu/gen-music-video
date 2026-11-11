# 每日抽象音乐视频自动生成系统

一个面向 macOS / Apple Silicon 的本地音乐视频流水线：每天依次生成中文、英文两条 **9:16 抽象音乐视频**，并通过 `launchd` 定时无人值守运行。

系统使用 Ollama 生成歌词和结构化视觉指令，使用 YoVoice / ACE-Step 生成歌曲，使用 ffmpeg 标准化音频，使用 NumPy 分析音频能量，最后由 Blender EEVEE 渲染画面并合成为 MP4。默认创作方向为**阳光、希望、成长、幸福与欢乐**：歌词和音乐风格必须积极向上、幸福欢乐，视觉默认明亮温暖。

> **验证边界**：本 README 提供完整部署与验证步骤，不代表当前环境已经实际完成模型下载、歌曲生成或 Blender 渲染。首次部署必须按[首次手动验证](#首次手动验证)逐项检查，确认中英两条链路均可用后再启用定时任务。

## 三分钟音乐与节奏视觉

歌曲固定请求 **180 秒**，使用 YoVoice 的 `modelOptions.ace_step.duration_seconds=180`（[官方选项目录](https://github.com/leemysw/yovoice/blob/main/internal/catalog/generation_options.json)）。生成后通过 ffprobe 检查实际时长：偏差超过 1 秒即报错；仅修正 1 秒以内的模型取整误差，输出精确 180 秒 PCM WAV，并添加自然淡出。不会循环短歌曲凑时长。运行环境需要 ffmpeg 和 ffprobe。

歌词按三分钟编排生成两轮主歌、副歌。视频保持 1080×1920、24 fps（180 秒共 4320 帧），增加三层细光环、空间粒子、柔和辉光、重叠转场和连续运镜。真实音频的频谱起音驱动光环脉冲，低中频控制主体，高频控制粒子亮度；这不是人声逐字对齐，段落视觉仍按时间均分。最终 MP4 使用 AAC 256k 和 faststart。

修改生成逻辑不会改变已有产物，需要重新运行流水线生成新作品。

## 功能特性

- **每日双语产出**：默认按 `zh`、`en` 顺序串行执行，中文路由到 `qwen3:8b`，英文路由到 `llama3.1:8b`。
- **积极快乐的结构化创作**：Ollama 使用 JSON Schema 约束标题、英文风格描述、歌词及每段视觉参数；默认歌词与音乐风格积极向上、幸福欢乐，失败时自动重试。
- **本地音乐生成**：通过 YoVoice HTTP 服务调用 `ace-step-1.5-q8`，歌词语言与目录语言保持一致。
- **音频驱动画面**：ffmpeg 输出 16-bit PCM、单声道、44.1 kHz 分析音频；NumPy 提取 RMS 与低、中、高频能量。
- **明亮温暖的抽象 3D 视觉**：Blender 根据段落配置创建粒子、晶体、圆环、流体元球或线框网格，以明亮温暖的默认配色驱动缩放、发光和摄像机运动。
- **可靠的无人值守编排**：单条失败不会阻止另一条执行；全部结束后以非零退出码汇总失败。
- **便于排查**：按日期保存日志和中间产物，可单独重跑任一语言，也可配置中间文件保留天数。

## 工作流程

```text
launchd / 手动执行
        │
        ▼
Ollama（zh → qwen3:8b，en → llama3.1:8b）
        │  lyrics.json：歌词 + style + sections 视觉指令
        ▼
YoVoice serve（ACE-Step）
        │  song.wav
        ▼
ffmpeg 标准化
        │  analysis.wav：PCM s16 / mono / 44100 Hz
        ▼
NumPy 音频分析 + Blender 4.2+ EEVEE Next
        │  silent_video.mp4
        ▼
ffmpeg 合流（视频流复制，音频编码为 AAC）
        │
        ▼
<安全化的歌词标题>_YYYY-MM-DD.mp4
```

音频分析和音视频合流不放在 Blender 内完成，可以避免无头模式下 Graph Editor 上下文和 VSE 状态带来的不稳定。每个阶段都可独立执行和重试。

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
│   ├── generate_music.py        # YoVoice 歌曲生成与音频下载
│   ├── audio_analysis.py        # PCM 音频读取及逐帧 FFT 分析
│   ├── render_scene.py          # Blender 无头场景构建与渲染
│   └── install_launchd.sh       # 渲染并加载 launchd 配置
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
    │   ├── analysis.wav         # 中间产物
    │   ├── silent_video.mp4     # 中间产物
    │   └── 向阳而生_2026-11-10.mp4  # 最终视频：安全化标题_日期
    └── en/
        └── ...
```

最终视频命名为 `<安全化的歌词title>_YYYY-MM-DD.mp4`。标题中的中文会保留，空白转为下划线，非法文件名字符替换为下划线。例如：

```text
~/Desktop/music-video/2026-11-10/zh/向阳而生_2026-11-10.mp4
~/Desktop/music-video/2026-11-10/en/Sunshine_and_Joy_2026-11-10.mp4
```

`YYYY-MM-DD` 是本次任务的 `DATE`，不是文件修改时间。

## 依赖已准备好时的快速启动

以下命令假定已执行 `uv sync --dev`，已配置 `scripts/.env`，Ollama、YoVoice 和 Blender 均可用：

```bash
cd "$HOME/Desktop/music-video"
LANGS='zh en' bash scripts/daily_pipeline.sh
```

运行时可在另一个终端跟踪日志：

```bash
tail -f "$HOME/Desktop/music-video/_logs/$(date +%F).log"
```

两种语言均显示 `OK` 后，用实际标题生成的日期后缀找到并打开最终视频：

```bash
DATE_STAMP="$(date +%F)"
DAY="$HOME/Desktop/music-video/$DATE_STAMP"
find "$DAY/zh" "$DAY/en" -maxdepth 1 -type f \
  -name "*_${DATE_STAMP}.mp4" -exec open {} +
```

该命令会打开当天两个语言目录中所有符合后缀的最终视频；若保留了同日的旧标题产物，也会一并打开。

如果使用了自定义 `MUSIC_VIDEO_ROOT` 或 `DATE`，请将上述路径替换为对应目录。快速启动只是运行入口，不代表真实生成已经验证成功。

## 环境要求

- macOS，推荐 Apple Silicon；需求目标为 M4 Pro / 24 GB 统一内存。
- **uv 0.11.12**，作为主依赖管理工具；项目使用 **Python 3.12**，`.python-version` 已固定为 `3.12`，`pyproject.toml` 要求至少 Python 3.10。
- Ollama，以及 `qwen3:8b`、`llama3.1:8b` 两个模型。
- 可访问的 YoVoice 服务和 ACE-Step 模型；仅首次准备模型或需要脚本自动拉起服务时才需要 YoVoice macOS arm64 CLI。
- ffmpeg。
- Blender 4.2 或更新版本，使用 EEVEE Next。

> 8B 语言模型、ACE-Step 和 Blender 都会占用较多统一内存。生成期间不要同时运行其他大型模型或高内存任务。

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
uv run --no-sync python -c 'import sys, ollama, requests, numpy; print(sys.version); print("dependencies OK")'
```

预期项目解释器为 **CPython 3.12.x**。不要使用系统 `/usr/bin/python3` 的 3.9 运行本项目。Blender 使用其自身内置的 Python，与 uv 的 `.venv` 是两套独立环境。

`uv sync --dev` 根据 `pyproject.toml` 创建 `.venv`，并同时安装运行和测试依赖；存在 `uv.lock` 时按锁文件同步。日常 Python 命令使用 `uv run ...`，无需先激活虚拟环境。`requirements.txt` 和 `requirements-dev.txt` 只为非 uv 用户保留兼容路径，不是本项目的推荐主路径。

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

### 5. 安装 ffmpeg 和 Blender

```bash
brew install ffmpeg
brew install --cask blender

ffmpeg -version
/Applications/Blender.app/Contents/MacOS/Blender --version
```

若 Blender 不在默认位置，通过 `BLENDER` 配置其可执行文件绝对路径。

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
"$HOME/Desktop/music-video/.venv/bin/python3" -c 'import ollama, requests'
```

文件中的路径必须是**展开后的绝对路径**，不要使用 `~` 或 `$HOME`。完整条目以 `config/pipeline.env.example` 为准。

| 变量 | 默认/示例 | 用途 |
| --- | --- | --- |
| `MUSIC_VIDEO_ROOT` | `/Users/YOURNAME/Desktop/music-video` | 项目数据、日志和每日产物根目录 |
| `PYTHON` | `/Users/YOURNAME/Desktop/music-video/.venv/bin/python3` | 执行 Python 脚本的解释器；launchd 必须配置此项 |
| `LANGS` | `zh en` | 语言及串行顺序，只支持 `zh`、`en`；调试可设为 `zh` |
| `THEME` | 各语言内置的“阳光、希望、成长、幸福与欢乐”主题 | 可选，覆盖歌词主题；默认内容必须积极向上、幸福欢乐 |
| `DATE` | 当天 `YYYY-MM-DD` | 可选，指定补跑日期 |
| `YOVOICE_HOST` / `YOVOICE_PORT` | `127.0.0.1` / `8080` | YoVoice 监听地址和端口 |
| `YOVOICE_API_BASE` | 由 host/port 推导 | 可选，显式覆盖完整 API 地址 |
| `YOVOICE_DATA_DIR` | `$MUSIC_VIDEO_ROOT/.yovoice-data` | YoVoice 模型与数据目录 |
| `YOVOICE_TOKEN_FILE` | `$MUSIC_VIDEO_ROOT/.yovoice-token` | Bearer Token 文件路径 |
| `YOVOICE_MODEL_ID` | `ace-step-1.5-q8` | 歌曲模型 |
| `BLENDER` | `/Applications/Blender.app/Contents/MacOS/Blender` | Blender 可执行文件 |
| `RETENTION_DAYS` | 默认不清理，模板示例为 `3` | 可选，清理超过指定天数的分析 WAV 和无声视频 |
| `NOTIFY_FAILURE` | `0` | 设为 `1` 时尝试发送 macOS 失败通知 |

`scripts/.env` 提供默认值，命令前显式传入的环境变量优先级更高，例如 `LANGS=zh bash scripts/daily_pipeline.sh` 会覆盖文件中的 `LANGS`。日常调试既可修改配置文件，也可使用命令前缀临时覆盖：

```bash
THEME='阳光、友谊与一起成长的快乐' LANGS='zh' bash scripts/daily_pipeline.sh
```

直接分步调用 Python 脚本不会自动读取此文件，需要先 `source scripts/.env` 或自行导出相关变量。

## 首次手动验证

首次部署不要直接交给 launchd。按 **Ollama → YoVoice → ffmpeg → 音频分析 → Blender → 合流** 的顺序验证，再运行完整流水线。

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

然后生成歌曲：

```bash
uv run python scripts/generate_music.py \
  --lyrics "$TEST_OUT/lyrics.json" \
  --output "$TEST_OUT/song.wav"
ffprobe -v error -show_streams -show_format "$TEST_OUT/song.wav"
```

若使用非默认地址，确保 `YOVOICE_API_BASE` 或 `YOVOICE_HOST` / `YOVOICE_PORT` 与健康检查地址一致。带歌词的音乐必须使用 ACE-Step / YuE2 一类支持歌曲生成的模型，不要改用只生成纯音乐的 Stable Audio。

### 3. 验证音频标准化与分析

```bash
ffmpeg -y -loglevel error -i "$TEST_OUT/song.wav" \
  -ac 1 -ar 44100 -sample_fmt s16 "$TEST_OUT/analysis.wav"
ffprobe -v error -show_entries \
  stream=codec_name,sample_rate,channels,bits_per_sample \
  -of default=noprint_wrappers=1 "$TEST_OUT/analysis.wav"
```

预期为 PCM 16-bit、44100 Hz、单声道。`audio_analysis.py` 由渲染脚本调用；需要单独诊断其参数时运行：

```bash
uv run python scripts/audio_analysis.py --help
```

若 `uv run` 提示缺少 NumPy，先重新执行 `uv sync --dev`。Blender 使用其自带的 Python 运行渲染脚本；如果只有 Blender 调用时报依赖错误，应单独检查该环境中的 NumPy 是否可用。

### 4. 验证 Blender 无头渲染

```bash
"${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}" \
  --background --factory-startup -noaudio \
  --python-exit-code 1 \
  --python "$MUSIC_VIDEO_ROOT/scripts/render_scene.py" -- \
  --lyrics-json "$TEST_OUT/lyrics.json" \
  --analysis-wav "$TEST_OUT/analysis.wav" \
  --output "$TEST_OUT/silent_video.mp4"

test -s "$TEST_OUT/silent_video.mp4"
```

`--python-exit-code 1` 不可省略，否则 Blender 遇到 Python 异常时仍可能返回成功。渲染结束后还要确认文件存在且非空。

### 5. 验证音视频合流

将 `lyrics.json` 的 `title` 按上述规则安全化，再作为最终文件名前缀。例如标题为“向阳而生”时：

```bash
SAFE_TITLE='向阳而生'  # 替换为实际 title 的安全化结果
FINAL_VIDEO="$TEST_OUT/${SAFE_TITLE}_$(date +%F).mp4"
ffmpeg -y -loglevel error \
  -i "$TEST_OUT/silent_video.mp4" -i "$TEST_OUT/song.wav" \
  -c:v copy -c:a aac -b:a 192k -shortest \
  "$FINAL_VIDEO"

test -s "$FINAL_VIDEO"
ffprobe -v error -show_streams -show_format "$FINAL_VIDEO"
```

打开视频抽查：有声、画面随节奏变化、竖屏比例为 9:16，且歌曲语言与目录一致。

### 6. 验证完整编排

分别验证中文和英文，再验证默认双语言任务：

```bash
LANGS='zh' bash scripts/daily_pipeline.sh
LANGS='en' bash scripts/daily_pipeline.sh
bash scripts/daily_pipeline.sh
```

确认两个目录中都有 `lyrics.json`、`song.wav` 和 `<安全化标题>_<DATE>.mp4`。两条任务应串行执行；若第一条失败，第二条仍应尝试，日志末尾通过 `fail=1` 和非零退出码汇总失败。

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

每种语言应以 `OK` 结束；失败时会记录 `FAILED`，整个脚本最终以非零状态退出。`RETENTION_DAYS` 控制旧分析音频和无声视频的自动清理，原始歌曲、最终 MP4、歌词及日志不会因该设置被当作中间文件删除。清理前仍建议先备份重要产物。

## 测试

主路径使用 uv 同步运行及测试依赖，然后任选标准库 `unittest` 或 `pytest`：

```bash
cd "$HOME/Desktop/music-video"
uv sync --dev
uv run python -m unittest discover -s tests -v
# 或者：uv run pytest -q
```

`requirements-dev.txt` 仅供非 uv 用户通过 pip 安装同一组开发依赖。这里仅提供测试命令，不声称当前仓库的测试已经通过。

单元测试覆盖歌词数据校验、音乐请求构造和音频分析等离线逻辑，不会替代以下真实集成验证：

- Ollama 模型可下载并能按 Schema 返回内容；
- YoVoice 可启动、鉴权、生成并返回音频；
- Blender 可在本机 Metal/EEVEE 环境无头渲染；
- ffmpeg 合流后的 MP4 可正常播放且音视频同步。

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
- 模型不存在：重新下载 `ace-step-1.5-q8`。
- 中文发音异常：检查 `lyrics.json` 的 `_lang`，不要手动让歌词语言与 `language` 参数不一致。

### `analysis.wav` 无法读取

重新执行标准化命令，并确认是 16-bit PCM，而不是 24-bit、float 或压缩 WAV。分析器需要可读的非空音频；0 字节文件和损坏下载不能被当作成功。

### Blender 返回异常或 MP4 为空

检查调用中是否包含 `--background --factory-startup -noaudio --python-exit-code 1`，并确认 `BLENDER` 指向真实可执行文件。查看当日日志中的 Python traceback；仅看到 Blender 进程返回还不够，必须执行 `test -s silent_video.mp4`。

### 视频时长或声音异常

最终合流使用 `-shortest`，视频与音频以较短者为准。若画面提前结束，先检查分析 WAV 和无声视频时长，再检查 Blender 帧数、帧率和音频采样信息：

```bash
# 在待检查的 zh/ 或 en/ 产物目录中执行；日期后缀匹配最终标题视频。
for file in song.wav analysis.wav silent_video.mp4 ./*_????-??-??.mp4; do
  [ -f "$file" ] || continue
  printf '%s: ' "$file"
  ffprobe -v error -show_entries format=duration \
    -of default=noprint_wrappers=1:nokey=1 "$file"
done
```

### 内存不足、渲染极慢或机器卡顿

不要并行运行两条流水线。关闭其他大模型、渲染任务和高内存应用；保留一个 YoVoice 服务和一个 Blender 实例。需要进一步降低负载时，可减少场景复杂度、渲染采样或粒子数量，但不要未经测试同时修改分辨率和渲染后端。

## 资源与运行时间参考

在 M4 Pro / 24 GB 的目标环境中，需求估算如下，实际时间随歌曲长度、模型、场景复杂度和系统负载变化：

| 阶段 | 单条估算 | 两条合计 |
| --- | ---: | ---: |
| Ollama 歌词与视觉 JSON | 30–60 秒 | 1–2 分钟 |
| YoVoice ACE-Step 歌曲 | 3–8 分钟 | 6–16 分钟 |
| ffmpeg 标准化 | 少于 5 秒 | 少于 10 秒 |
| Blender 1080×1920 / 24 fps 渲染 | 4–10 分钟 | 8–20 分钟 |
| ffmpeg 合流 | 少于 10 秒 | 少于 20 秒 |
| **总计** | — | **约 15–40 分钟** |

这些数字不是性能承诺。默认输出为 1080×1920、24 fps；两条任务必须串行，避免 Metal GPU 和统一内存争用。

## 部署验收清单

- [ ] `ollama list` 中同时存在 `qwen3:8b` 和 `llama3.1:8b`。
- [ ] YoVoice 健康检查通过，ACE-Step 模型已下载，Token 文件权限为 `600`。
- [ ] ffmpeg 与 Blender 版本命令正常，Blender 路径与配置一致。
- [ ] 中文手动流水线成功，英文手动流水线成功。
- [ ] 两个最终 MP4 均非空，有声音，画面随节奏变化，比例为 9:16。
- [ ] `lyrics.json` 的语言、模型元字段、YoVoice 发音语言和输出目录一致。
- [ ] 已执行 `uv sync --dev`，运行 `uv run python -m unittest discover -s tests -v` 或 `uv run pytest -q`，并处理所有失败。
- [ ] plist 通过 `plutil -lint`，launchd 中能看到任务。
- [ ] 手动触发 launchd 后，日志中中文和英文均以 `OK` 结束。
- [ ] 已确认定时任务的 `PATH`、Python 绝对路径、根目录和 Token 路径不依赖当前终端。

完成以上检查后，再将系统视为可无人值守运行。
