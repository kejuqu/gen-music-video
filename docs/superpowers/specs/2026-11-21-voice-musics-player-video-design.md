# Voice-Musics 播放器风格视频 — 设计文档

日期：2026-11-21
状态：已获用户批准（布局、歌手名、歌词时间轴方式均已确认）

## 意图与背景

用户在 `voice-musics/<歌名>/` 下存放外部音乐，每个子文件夹包含一首歌：一个 wav + 一个 `lyrics.txt`（可能含 `[verse]` 等段落标记，可能是中文歌词）。需求：为每首歌直接生成"音乐播放器界面"风格的视频，参考图为 frosted-glass 音乐播放器（标题/歌手、环形频谱 + 黑胶唱片 + 封面、漂浮音符、进度条、模糊渐变背景）。

关键决策（用户已确认）：

- **只出手机端竖版（9:16，720×1280），不生成 16:9**。

- **歌手名统一为「一千柯橘」**，歌名 = 文件夹名。
- **歌词位置由专业设计决定**（本文件的布局章节）。
- **歌词时间轴用 mlx-whisper 自动对齐**（复用项目现有方案，支持中文），实现逐字 karaoke 高亮。

## 目标

- 一条命令处理 `voice-musics/` 下所有歌曲文件夹：歌词对齐 → 渲染 9:16（720×1280）无声画面 → ffmpeg 合流 → 归档到 `outputs-videos/`。
- **每首歌背景颜色不同**：由文件夹名哈希播种的随机主色相，同一首歌重跑结果一致（确定性）。
- **歌词跟随演唱**：当前句 karaoke 式逐字高亮（中文按字、英文按词）；下一句以 30% 透明度预显；`[verse]` 等段落标记不上屏。
- **音符跳动**：♪ ♫ 𝄞 矢量音符粒子从画面底部升起，大小/透明度/上升速度随频谱能量呼吸。
- 进度条与已播/总时长用真实音频时长驱动。
- 画面风格遵循设计系统推荐：深色 OLED 底 + 玻璃拟态 + 高对比文字 + 微光效果。

## 非目标

- 不做"英语单词学习卡片"（早期讨论提过，本次迭代不含）。
- 不做参考图右下角的 "Up Next" 播放列表、底部迷你波形条（避免画面拥挤）。
- 不改动现有每日流水线（daily_pipeline）与 `render_visualizer.py` 的经典黑胶布局；新渲染器是独立脚本，复用其共享辅助函数。
- 不移动/删除用户的原始 wav 与文件夹（不同于 pure 流程的归档方式）。
- 不接 launchd 定时；通过 `make voice-videos` 手动触发。

## 反目标（做了就算失败）

- 用 emoji/字符字形当图标 —— 所有图标（播放控制、音符）必须用矢量路径绘制。
- 让 whisper 自由转写文本替代用户提供的 `lyrics.txt` —— whisper 只用于**对齐**已知歌词，不改写歌词。
- 段落标记 `[verse]`/`[chorus]` 等出现在画面上；编曲注释行（如 `（8小节，轻柔钢琴…）`）等非歌词文本不得作为卡拉 OK 行上屏。
- 每次渲染背景色随机不可复现 —— 随机性必须由歌名播种。

## 构建者审查的裁定（2026-11-21 增补）

设计文档经独立构建者审查后，以下四点按上述方向裁定并写入：

1. **歌词文件非歌词行**：`lyrics.txt` 中整行被括号包裹的注释行（如 `（8小节，轻柔钢琴+淡淡的木吉他）`）以及任何 `[xxx]` / `[xxx n]` 变体段落标记（含空格、编号，大小写不敏感）一律在**播放器本地解析器**中剔除后再进入对齐，**不改动** `lyric_timing.py` 现有的 `SECTION_TAG`（避免影响每日流水线）。剔除发生在喂给 whisper 之前，防止注释行污染全局字符对齐。
2. **对齐质量验收门**：整首歌中映射覆盖率 ≥0.5 的歌词行不足 50% 时（歌词与音频不匹配、纯伴奏、语言检测错误等），判定该首失败 —— `voice_videos.sh` 记 FAILED、跳过该首并计入批处理退出码，**不产出**歌词乱飞的坏成片；用户修正后删 `.voice-work/<stem>/` 重跑。行级低覆盖仍走现有"插在前一行之后"的回退。
3. **timing 缓存失效键**：`timing.json` 复用条件为同时比 `lyrics.txt` **和 wav 文件**更新（mtime 比较）；wav 被替换/重导出即自动重新对齐。
4. **中文词内逐字插值**：whisper 对中文只给词级时间跨度（词内各字同 start/end），词内各字按**线性插值**得到逐字时间，实现真正的逐字 karaoke 高亮；英文按词粒度天然对齐。

## 约束

- macOS Apple Silicon，Python 3.12（`.venv`），PIL / NumPy / visgen / mlx-whisper / ffmpeg（优先 `h264_videotoolbox`）。
- 30 fps；仅 9:16 = 720×1280；h264 + AAC 192k；`-movflags +faststart`；合流用 `-shortest -c:v copy`。
- 渲染性能目标：与现有渲染器同量级（M4 Pro 上每首歌约 1 分钟内）。

## 实现方式（已决定的）

| 部分 | 方案 |
| --- | --- |
| 渲染器 | 新脚本 `scripts/render_player.py`；从 `render_visualizer.py` 导入复用：`perceptual_gain`、`encode_ffmpeg`/`_videotoolbox_usable`/`_has_videotoolbox`、`wrap_text`、`_has_cjk`、`ZH_FONT`/`EN_FONT`、`make_cover`（传入合成 metadata 复用其波形封面生成） |
| 歌词 token 时间 | 扩展 `scripts/lyric_timing.py`：新增 `compute_timing_tokens()`，在现有 `_align_by_chars` 的字符映射上把映射字符时间聚合为 token（中文=字，英文=词）级时间；`compute_timing()` 保持原签名不变 |
| 批处理 | 新脚本 `scripts/voice_videos.sh`，结构照搬 `pure_videos.sh`（逐首串行、单首失败不阻塞、退出码汇总） |
| 入口 | `Makefile` 增加 `voice-videos` 目标 |
| 文档 | README 新增"外部音乐播放器风格成片（voice-musics）"章节 |
| 测试 | `tests/test_render_player.py` 新增离线单测；扩展现有 lyric_timing 测试 |

### 每首歌的确定性配色

`seed = sha1(文件夹名)`；`hue = seed 导出的 [0,1) 值`；调色板 4 色：`hsv(hue, 0.75, 1)`、`hsv(hue±40°)`、`hsv(hue+0.5, 0.6, 0.95)`。背景 = 深暗基调（亮度 8–14%）+ 该色相的两个大模糊光斑；封面、环形频谱、进度条光点共用同一调色板。

### 歌词语言检测

歌词文本含 CJK 字符 → `zh`（走 `ZH_FONT`），否则 `en`。作为 `--lang` 可选项的默认值。

## 画面布局规格

仅 9:16（720×1280）一种比例：

- 卡片：约 620×640，居中偏上（顶部留白 ~70px），内含歌名/歌手/唱片/环形频谱/控制/进度条，同 16:9 规格。
- **歌词区在卡片下方**（独立区域，不压卡片）：三行式 —— 上一句（22px，白 28%）/ 当前句逐字高亮（34px）/ 下一句（22px，白 35%）；最大宽度 640，超长最多折 2 行。

### karaoke 逐字高亮

- token  sung 比例 f ∈ [0,1]：颜色从"白 55%"线性过渡到暖金 `#FFD166`，f=1 后保持金色 + 柔和文字光晕（绘制 2–3 次偏移叠加实现 glow，不用 emoji）。
- token 时间来自 `compute_timing_tokens()`；行内未映射 token 在该行时间范围内均分回退。

### 音符粒子

- 预渲染 3 种矢量音符精灵（八分音符、双音 beam 音符、高音谱号简化形），白色，种子化随机。
- 每帧 10–14 个活跃粒子：从画面底部两侧外升起，大小 26–64px，透明度 50–120，水平正弦摇摆，上升速度 = 基础值 × (0.6 + 能量)；能量来自环形频谱 bar 均值（`perceptual_gain` 后）。

## 流水线规格（voice_videos.sh）

对每个 `voice-musics/<stem>/`：

1. 目录内必须恰好一个 `*.wav` 和一个 `lyrics.txt`，否则记失败并跳过（不阻塞其他歌）。
2. 工作目录 `.voice-work/<stem>/`（跨运行保留）：`timing.json` 若比 `lyrics.txt` 新则复用，否则重新 whisper 对齐（用户改歌词后删工作目录或自动失效重算）；`cover.png` 同理缓存。
3. `render_player.py --song-wav ... --lyrics-txt ... --title <stem> --artist 一千柯橘 --output silent_9x16.mp4 --aspect 9:16`。
4. ffmpeg 合流（`-c:v copy -c:a aac -b:a 192k -shortest`）。
5. 成品输出 `outputs-videos/<stem>_player_9x16.mp4`（加 `_player` 前缀避免与 pure 流程同名冲突）；**源 wav 与文件夹原地保留**。
6. 日志 `_logs/voice-videos-<日期>.log`；全部结束后以非零退出码汇总失败数。

环境变量覆盖：`VOICE_MUSICS_DIR`、`OUTPUT_VIDEOS_DIR`、`VOICE_WORK_DIR`、`PYTHON`、`ARTIST`（默认"一千柯橘"）。

## 测试（离线）

- `tests/test_render_player.py`：
  - 配色确定性：同 seed → 同调色板；不同歌名 seed → 色相明显不同。
  - `lyrics.txt` 解析：去段落标记、去空行、保留顺序。
  - 语言检测：中文/英文歌词。
  - token 化：中文按字、英文按词、中英混合。
  - karaoke token 时间聚合：模拟字符映射验证 sung 比例计算与均分回退。
  - 布局 sanity：所有元素坐标在 720×1280 画布边界内；歌词区不与卡片重叠。
  - 音符粒子：同 seed 序列可复现。
- `tests/test_lyric_timing.py` 扩展：`compute_timing_tokens` 输出结构（token 时间单调、不越出行范围、未映射回退）。
- 集成验证（手动）：对 `voice-musics/平凡底片/` 实跑一次，人工抽查：背景色与其他歌不同、歌词逐字跟唱、进度条走时、成片有声且画面随节奏变化。

## 留给实现者的小决定（已标记为我的选择）

- 精确像素坐标按上述范围在实现中微调。
- 播放控制图标画法：圆形底 + 三角形/双竖线矢量字形；🔀/🔁 用简化交叉箭头/回环箭头路径。
- 封面复用 `make_cover()` 波形风格，调色板传合成 metadata（`{"sections":[{"visual":{"palette":[...]}}]}`）。
