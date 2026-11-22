# Voice-Musics 播放器风格视频 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `voice-musics/<歌名>/` 下的外部音乐（wav + lyrics.txt）批量生成 9:16 竖版"音乐播放器"风格视频（720×1280），含逐字 karaoke 歌词、环形频谱、旋转黑胶、漂浮音符、真实进度条，每首歌确定性不同背景色，歌手名「一千柯橘」。

**Architecture:** 新增独立渲染器 `scripts/render_player.py`（复用 `render_visualizer.py` 的 `perceptual_gain`/`encode_ffmpeg`/`wrap_text`/字体常量，不改其布局）；新增 `scripts/voice_lyrics.py` 做播放器本地歌词解析；扩展 `lyric_timing.py` 增加 token 级时间（`compute_timing` 签名不变）；新增 `scripts/voice_videos.sh` 批处理（结构照搬 `pure_videos.sh`）。

**Tech Stack:** Python 3.12（uv venv）、PIL、NumPy、visgen（AudioProcessor）、mlx-whisper、ffmpeg（h264_videotoolbox 优先）、bash、unittest。

**Spec:** `docs/superpowers/specs/2026-11-21-voice-musics-player-video-design.md`（所有取值出自该文档；冲突时以 spec 为准）

## Global Constraints

- 只出 9:16 = 720×1280，30 fps，h264 + AAC 192k，`-movflags +faststart`，合流 `-shortest -c:v copy`。
- 所有随机性必须由歌名（文件夹名）播种，同歌重跑画面一致。
- 禁用 emoji/字符字形当图标；音符与播放控制全部矢量绘制。
- 歌手名默认「一千柯橘」（`voice_videos.sh` 的 `ARTIST` 环境变量可覆盖）；歌名 = 文件夹名。
- 不得改动 `lyric_timing.py` 的 `SECTION_TAG`、`extract_lines`、`compute_timing` 现有行为（每日流水线依赖）。
- 运行/测试用 `uv run`；测试一律 `unittest`，文件头 `sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))`。
- 成品命名 `outputs-videos/<stem>_player_9x16.mp4`；源 wav 与文件夹原地保留。
- 段落标记与整行括号注释不得出现在画面或喂给对齐的文本中。

## Review Focus

1. **歌词文件混入非歌词行**（`[verse 1]` 带空格/编号、`（8小节，…）` 整行注释）——必须剔除且不吞真实歌词（Task 1）。
2. **缓存 timing.json 对应旧 wav**（用户替换音频）——必须自动失效重对齐（Task 5）。
3. **歌词与音频不匹配**——覆盖率 <50% 的歌判失败跳过，不产出乱字幕成片（Task 2 覆盖率函数；Task 6 集成）。
4. **中文 whisper 只给词级时间戳**——逐字高亮依赖词内线性插值（Task 2）。
5. **9:16 超长歌词行**——折行 ≤2 行，所有元素不越出 720×1280（Task 4）。

---

### Task 1: 播放器本地歌词解析与语言检测

**Files:**
- Create: `scripts/voice_lyrics.py`
- Test: `tests/test_voice_lyrics.py`

**Interfaces:**
- Produces:
  - `parse_voice_lyrics(text: str) -> list[str]` — 剔除空行、任何 `[...]` 段落标记（含空格/编号/未知名，大小写不敏感）、整行被 `（...）`/`(...)` 包裹的注释行；保留其余行原文（不去内部空格）。
  - `detect_language(lines: list[str]) -> str` — 任一字符落在 `一`-`鿿` 返回 `"zh"`，否则 `"en"`。

- [ ] **Step 1: 写失败测试**

```python
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from voice_lyrics import parse_voice_lyrics, detect_language

class ParseTests(unittest.TestCase):
    def test_strips_bracket_tags_with_space_and_number(self):
        text = "[verse 1]\n闹钟又一次 卡在六点半\n[Pre-Chorus]\n好多委屈\n[chorus]\n我只是一个\n"
        self.assertEqual(parse_voice_lyrics(text), ["闹钟又一次 卡在六点半", "好多委屈", "我只是一个"])

    def test_strips_whole_line_parenthetical_annotation(self):
        text = "（8小节，轻柔钢琴+淡淡的木吉他）\n闹钟又一次\n(outro solo)\n好好活着\n"
        self.assertEqual(parse_voice_lyrics(text), ["闹钟又一次", "好好活着"])

    def test_keeps_inline_parenthetical_in_lyric_line(self):
        text = "我只是一个 (最普通的人)\n"
        self.assertEqual(parse_voice_lyrics(text), ["我只是一个 (最普通的人)"])

    def test_skips_empty_and_strips_whitespace(self):
        self.assertEqual(parse_voice_lyrics("  第一行  \n\n   \n第二行"), ["第一行", "第二行"])

class LanguageTests(unittest.TestCase):
    def test_chinese(self):
        self.assertEqual(detect_language(["闹钟又一次 卡在六点半"]), "zh")

    def test_english(self):
        self.assertEqual(detect_language(["Just an ordinary person"]), "en")
```

- [ ] **Step 2: 跑测试确认失败**

`uv run python -m unittest tests.test_voice_lyrics -v` → ImportError

- [ ] **Step 3: 实现 `scripts/voice_lyrics.py`**

两个函数。括号行正则：`^\s*[（(].*[）)]\s*$` 且整行仅此一对括号（即去掉首尾括号后不含另一套括号）；段落标记正则：`^\s*\[[^\]]*\]\s*$` 直接涵盖空格/编号/任意名——无需复用 lyric_timing 的 SECTION_TAG。

- [ ] **Step 4: 跑测试确认通过** → PASS；`uv run python -m unittest discover -s tests` 全绿

- [ ] **Step 5: Commit** `feat: add voice_lyrics parser for external-song lyric files`

### Task 2: token 级歌词时间 + 对齐覆盖率

**Files:**
- Modify: `scripts/lyric_timing.py`（内部重构 `_align_by_chars` 暴露映射；新增 2 个公开函数；`compute_timing`/`SECTION_TAG` 行为不变）
- Test: `tests/test_lyric_timing.py`（追加 TestCase）

**Interfaces:**
- Consumes: `voice_lyrics.parse_voice_lyrics`（Task 1，由调用方传入已解析行）
- Produces:
  - `compute_timing_tokens(song_path: Path, lines: list[str], language: str) -> list[dict]` — 每项 `{start, end, text, tokens}`，`tokens: list[{text, start, end}]`。token 粒度：含 CJK 的行按字、纯 ASCII 行按词。行内未映射 token 在该行 [start,end] 均分。
  - `alignment_coverage(timed: list[dict]) -> float` — `coverage>=0.5` 的行占比。

- [ ] **Step 1: 写失败测试**（追加到 test_lyric_timing.py，数据全离线 mock）

```python
from lyric_timing import alignment_coverage, tokens_from_alignment

class TokenTests(unittest.TestCase):
    def test_zh_tokens_split_per_char_with_intra_word_interpolation(self):
        chars = [("我",0.0,0.4),("只",0.4,0.8),("是",0.8,1.2),("一",1.2,1.6),("个",1.6,2.0)]
        toks = tokens_from_alignment("我是一个", chars)  # 词"我是一个"跨度0~2.0，词内5字线性插值
        self.assertEqual([t["text"] for t in toks], ["我","只","是","一","个"])
        self.assertAlmostEqual(toks[1]["start"], 0.4)
        self.assertAlmostEqual(toks[1]["end"], 0.8)

    def test_en_tokens_split_per_word(self):
        chars = [("j",0.0,0.3),("u",0.0,0.3),("s",0.0,0.3),("t",0.0,0.3),("a",0.4,0.8)]
        toks = tokens_from_alignment("Just a", chars)
        self.assertEqual([t["text"] for t in toks], ["Just","a"])

    def test_unmapped_tokens_split_evenly(self):
        toks = tokens_from_alignment("我是一个", [], line_start=1.0, line_end=3.0)
        self.assertAlmostEqual(toks[0]["start"], 1.0)
        self.assertAlmostEqual(toks[-1]["end"], 3.0)

class CoverageTests(unittest.TestCase):
    def test_coverage_ratio(self):
        timed = [{"start":0,"end":1,"text":"a","mapped":True},
                 {"start":1,"end":1,"text":"b","mapped":False}]
        self.assertAlmostEqual(alignment_coverage(timed), 0.5)
```

（实现时 `tokens_from_alignment` 的精确签名以测试为准——上面假定它接收 原文行+该行的映射字符列表+(可选)行起止；`mapped` 标记进 timed 行以支撑覆盖率。）

- [ ] **Step 2: 跑测试确认失败**（ImportError）

- [ ] **Step 3: 实现**

重构 `_align_by_chars`：把每行 mapped 的 transcript char 索引按行保存（现在只算 min/max 后丢弃），返回 `(timed, per_line_chars)`；`compute_timing` 继续只用 timed，行为不变。新增 `_line_tokens(line, chars, line_start, line_end)`：CJK 行按字切 token、ASCII 行按空格切词；每个 token 取其覆盖的映射字符的 min(start)/max(end)，无映射则在行区间内均分。`compute_timing_tokens` = 转写→`align_lines` 取 per-line chars→逐行 `_line_tokens`→`fill_gaps`。`alignment_coverage` 按 `mapped` 标记计数。

- [ ] **Step 4: 测试 PASS + 既有 lyric_timing 测试全绿**（证明无回归）

- [ ] **Step 5: Commit** `feat: add token-level lyric timing with alignment coverage`

### Task 3: 确定性配色 + 静态图层构建

**Files:**
- Create: `scripts/render_player.py`
- Test: `tests/test_render_player.py`

**Interfaces:**
- Consumes: `render_visualizer.make_cover(data, path)`、`ZH_FONT`/`EN_FONT`（导入复用）
- Produces:
  - `LAYOUT = Layout(aspect="9:16", width=720, height=1280, ...)` — 模块级常量；`Layout` 为 frozen dataclass，字段至少含 `width/height/aspect/disc_radius/label_radius/bar_count/bar_max_len/card_box/cover_box/lyric_zone`，坐标数值来自 spec 布局章节。
  - `song_palette(seed_text: str) -> list[tuple[int,int,int]]` — 4 色，seed=`sha1(seed_text.encode()).hexdigest()`。
  - `build_background(palette, layout) -> Image.Image`（RGB）— 深色调和渐变 + 2 个大模糊光斑（色相 = palette[0]/palette[2]）+ 暗角。
  - `build_card(background, layout) -> Image.Image`（RGBA）— 磨砂卡片：裁剪背景卡片区 → GaussianBlur(25) → 压暗 → 白色叠加 alpha≈26 → 圆角蒙版 + 细白边。
  - `cover_for(title, language, palette, path) -> Path` — 合成 `{"title","_lang","sections":[{"visual":{"palette":[hex...]}}]}` 调 `make_cover`。
  - `build_disc_base(layout)` / `build_label_layer(cover, layout)` — 从 render_visualizer 复制思路实现（黑胶体+纹路 / 封面标签+光泽+轴孔），render_player 本地版本。

- [ ] **Step 1: 写失败测试**

```python
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import render_player as rp

class PaletteTests(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(rp.song_palette("平凡底片"), rp.song_palette("平凡底片"))

    def test_distinct_between_songs(self):
        a, b = rp.song_palette("平凡底片"), rp.song_palette("love you")
        self.assertNotEqual(a[0], b[0])

class LayerTests(unittest.TestCase):
    def test_background_size_and_dark(self):
        bg = rp.build_background(rp.song_palette("x"), rp.LAYOUT)
        self.assertEqual(bg.size, (720, 1280))
        px = bg.convert("L").getpixel((360, 640))
        self.assertLess(px, 90)  # 背景必须暗

    def test_card_within_canvas(self):
        bg = rp.build_background(rp.song_palette("x"), rp.LAYOUT)
        card = rp.build_card(bg, rp.LAYOUT)
        x0, y0, x1, y1 = rp.LAYOUT.card_box
        self.assertTrue(0 <= x0 < x1 <= 720 and 0 <= y0 < y1 <= 1280)
```

- [ ] **Step 2: 跑测试确认失败** → ImportError

- [ ] **Step 3: 实现 `scripts/render_player.py` 骨架**

模块头导入 PIL/numpy/colorsys/hashlib，以及 `from render_visualizer import make_cover, ZH_FONT, EN_FONT`（复用，不重写封面生成与字体）。`build_card` 圆角蒙版用 `ImageDraw.rounded_rectangle` 画 L 蒙版。调色板：hue 由 seed 前 8 位 hex 转 [0,1)；4 色 = `hsv(h,0.75,1)`、`hsv(h+40°/360,0.7,0.95)`、`hsv(h-40°/360,0.8,1)`、`hsv(h+0.5,0.6,0.95)` 转 RGB。

- [ ] **Step 4: 测试 PASS**；`uv run python -c "import render_player"` 确认可导入

- [ ] **Step 5: Commit** `feat: render_player theme palette and static layers`

### Task 4: 矢量精灵 + karaoke 绘制 + 进度条

**Files:**
- Modify: `scripts/render_player.py`
- Test: `tests/test_render_player.py`（追加）

**Interfaces:**
- Produces:
  - `note_sprites() -> list[Image.Image]` — 3 个音符精灵（八分音符/双音连符/高音谱号简化形），白色 RGBA，高度 ≈64px，矢量路径绘制。
  - `control_icons() -> dict[str, Image.Image]` — 键 `"prev","pause","next","shuffle","repeat"`，白色字形 RGBA。
  - `karaoke_color(f: float, palette) -> tuple[int,int,int]` — sung 比例 f：白 55% 线性插值到暖金 `#FFD166`。
  - `draw_karaoke_line(draw, line_tokens, font, center, t, palette, max_width)` — 按 token 当前 sung 比例逐 token 着色（无 glow 时也可过；glow = 同色 2px 偏移重绘一次）。
  - `format_time(seconds: float) -> str` — `m:ss`。
  - `layout_lyric_zone(layout, prev_line, cur_tokens, next_line, fonts) -> list[(text_or_tokens, y, size, alpha)]` — 三行式（上一行淡/当前句高亮/下一行淡），当前句折行 ≤2，全部坐标在 `LAYOUT.lyric_zone` 内。
  - `draw_progress(draw, layout, t, duration)` — 轨道 + 已填充段 + 发光 knob + 两侧 `format_time`。

- [ ] **Step 1: 写失败测试**

```python
class SpriteTests(unittest.TestCase):
    def test_three_note_sprites(self):
        sprites = rp.note_sprites()
        self.assertEqual(len(sprites), 3)
        for s in sprites:
            self.assertEqual(s.mode, "RGBA")
            self.assertGreaterEqual(s.height, 48)

    def test_control_icon_keys(self):
        self.assertEqual(set(rp.control_icons()), {"prev","pause","next","shuffle","repeat"})

class KaraokeTests(unittest.TestCase):
    def test_color_unsung_is_dim_white(self):
        c = rp.karaoke_color(0.0, rp.song_palette("x"))
        self.assertLess(max(c), 200)

    def test_color_sung_is_gold(self):
        c = rp.karaoke_color(1.0, rp.song_palette("x"))
        self.assertGreater(c[0], 220)   # 红分量高
        self.assertGreater(c[1], 170)   # 绿分量高
        self.assertLess(c[2], 140)      # 蓝分量低

    def test_color_midpoint_between(self):
        mid = rp.karaoke_color(0.5, rp.song_palette("x"))
        self.assertLess(mid[0], rp.karaoke_color(1.0, rp.song_palette("x"))[0])

    def test_format_time(self):
        self.assertEqual(rp.format_time(135.0), "2:15")
        self.assertEqual(rp.format_time(8.0), "0:08")

class LyricZoneTests(unittest.TestCase):
    def test_zone_entries_inside_canvas(self):
        entries = rp.layout_lyric_zone(rp.LAYOUT, "上一句", [{"text":"a","start":0,"end":1}], "下一句", None)
        for e in entries:
            self.assertTrue(0 <= e["y"] <= 1280)

    def test_long_line_wraps_to_two(self):
        long_tokens = [{"text": ch, "start": i*0.1, "end": (i+1)*0.1} for i, ch in enumerate("我"*60)]
        entries = rp.layout_lyric_zone(rp.LAYOUT, None, long_tokens, None, None)
        cur = [e for e in entries if e.get("karaoke")]
        self.assertLessEqual(len(cur), 2)
```

- [ ] **Step 2: 跑测试确认失败** → AttributeError

- [ ] **Step 3: 实现**

`karaoke_color`：暗白 `(255,255,255)×0.55` 与 `(255,209,102)` 逐通道 lerp。音符用 `ImageDraw` 画：符头椭圆 + 符杆矩形 + 旗/beam 多边形；谱号用两条弧线+螺旋近似（简化形即可，spec 已授权）。`layout_lyric_zone` 用 `render_visualizer.wrap_text` 折当前句（≤2 行），三行 y 由 `LAYOUT.lyric_zone` 均分，返回 dict 列表。

- [ ] **Step 4: 测试 PASS**

- [ ] **Step 5: Commit** `feat: render_player sprites, karaoke and progress widgets`

### Task 5: 渲染主循环 + 音符粒子 + CLI 入口

**Files:**
- Modify: `scripts/render_player.py`
- Test: `tests/test_render_player.py`（追加）

**Interfaces:**
- Consumes: `lyric_timing.compute_timing_tokens`（Task 2）、`voice_lyrics.parse_voice_lyrics`（Task 1）、`render_visualizer.encode_ffmpeg/perceptual_gain/wrap_text`（复用）
- Produces:
  - `class NoteParticles` — `NoteParticles(seed_text: str, sprites: list, layout)`；`update(energy: float) -> list[(sprite, x, y, alpha)]`；种子化确定性。
  - `timing_cache_valid(timing_path, lyrics_txt_path, wav_path) -> bool` — 存在且 mtime 同时晚于两个源文件。
  - `render(song_wav, output, title, artist, timing, palette, layout, duration=None)` — 主循环：背景 → 光斑 glow（能量驱动）→ 卡片 → 唱片+标签旋转 → 环形频谱（`bar_count` 根，调色板渐变，对数增益）→ 控制图标 → 进度条 → 三行歌词（karaoke）→ 音符粒子；帧写 `encode_ffmpeg`。
  - `main()` — argparse：`--song-wav`、`--lyrics-txt`、`--title`、`--artist`（默认「一千柯橘」）、`--output`、`--duration`、`--timing`（缓存路径，可选）。

- [ ] **Step 1: 写失败测试**

```python
class ParticleTests(unittest.TestCase):
    def test_deterministic_sequence(self):
        a = rp.NoteParticles("平凡底片", rp.note_sprites(), rp.LAYOUT)
        b = rp.NoteParticles("平凡底片", rp.note_sprites(), rp.LAYOUT)
        sa, sb = a.update(0.5), b.update(0.5)
        self.assertEqual([(s[1], s[2], s[3]) for s in sa], [(s[1], s[2], s[3]) for s in sb])
        self.assertTrue(0 < len(sa) <= 14)

    def test_energy_speeds_rise(self):
        p = rp.NoteParticles("平凡底片", rp.note_sprites(), rp.LAYOUT)
        p.update(0.0); y_low = p.update(0.0)[0][2]
        p2 = rp.NoteParticles("平凡底片", rp.note_sprites(), rp.LAYOUT)
        y_high = p2.update(1.0)[0][2]
        self.assertLess(y_high, y_low)  # 高能时升得更多(y 向上减小)

class CacheTests(unittest.TestCase):
    def _touch(self, path, mtime):
        path.write_text("x"); import os; os.utime(path, (mtime, mtime))

    def test_invalid_when_wav_newer(self):
        import tempfile, time
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); tp = d/"timing.json"; lw = d/"lyrics.txt"; wv = d/"a.wav"
            now = time.time()
            self._touch(lw, now-30); self._touch(wv, now-20); self._touch(tp, now-10)
            self.assertTrue(rp.timing_cache_valid(tp, lw, wv))
            self._touch(wv, now)          # wav 更新
            self.assertFalse(rp.timing_cache_valid(tp, lw, wv))

    def test_invalid_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(rp.timing_cache_valid(Path(d)/"t.json", Path(d)/"l", Path(d)/"w"))
```

- [ ] **Step 2: 跑测试确认失败** → AttributeError

- [ ] **Step 3: 实现主循环与 CLI**

帧结构与 `render_visualizer.render` 同构（复用 `AudioProcessor(str(wav), fps=30)`、`perceptual_gain`、`encode_ffmpeg`）。环形频谱：半径 `disc_radius+14` 起、`bar_max_len` 止，颜色 `palette[i % 4]` 渐变（不彩虹漂移——确定性要求，色相固定）。粒子：12 个槽位，各自循环寿命，`y = bottom - rise*t*(0.6+0.8*energy)`，x 正弦摇摆；越界回底。`main()` 流程：`parse_voice_lyrics` → `detect_language` → 缓存有效则读 `timing.json`，否则 `compute_timing_tokens` 并写缓存（JSON 带 `tokens`）→ `cover_for` → `render`。覆盖率 <0.5 时 `raise SystemExit("alignment coverage below threshold")`（退出码非零，voice_videos.sh 据此判失败）。

- [ ] **Step 4: 测试 PASS** + `bash -n` 无关；`uv run python -m compileall -q scripts` 通过

- [ ] **Step 5: 手动冒烟验证**（非自动化，记入提交说明）

```bash
uv run python scripts/render_player.py --song-wav "voice-musics/平凡底片/20261119-161231_Warm-mellow-intimate_d11bb2b3.wav" \
  --lyrics-txt "voice-musics/平凡底片/lyrics.txt" --title "平凡底片" \
  --output /tmp/player_smoke.mp4 --duration 15
ffprobe -v error -show_entries format=duration -of csv=p=0 /tmp/player_smoke.mp4
```

预期 ≈15s、720×1280；肉眼抽帧确认卡片/唱片/频谱/歌词/音符/进度条齐全。

- [ ] **Step 6: Commit** `feat: render_player main loop with note particles and CLI`

### Task 6: 批处理脚本 + Makefile + README + 集成

**Files:**
- Create: `scripts/voice_videos.sh`
- Modify: `Makefile`（追加 `voice-videos` 目标）
- Modify: `README.md`（新增「外部音乐播放器风格成片（voice-musics）」章节，对齐 pure-musics 章节写法）
- Test: `tests/test_daily_pipeline.py` 同风格的 shell 语法检查入 `Makefile check`（`bash -n scripts/voice_videos.sh`）

**Interfaces:**
- Consumes: `render_player.main()`（Task 5）
- Produces: `scripts/voice_videos.sh` — 环境变量 `VOICE_MUSICS_DIR`（默认 `$ROOT/voice-musics`）、`OUTPUT_VIDEOS_DIR`、`VOICE_WORK_DIR`（默认 `$ROOT/.voice-work`）、`PYTHON`、`ARTIST`（默认 `一千柯橘`）；每首：校验恰好 1 个 wav + `lyrics.txt` → `.voice-work/<stem>/`（保留缓存，不每次清空）→ 渲染 → 合流 `outputs-videos/<stem>_player_9x16.mp4`；源文件原地保留；单首失败不阻塞；退出码汇总；日志 `_logs/voice-videos-<日期>.log`。

- [ ] **Step 1: 写失败"测试"= 语法与目标检查**

```bash
# 先断言失败（脚本不存在）
bash -n scripts/voice_videos.sh        # 预期: No such file
grep -q "voice-videos:" Makefile       # 预期: 无输出
```

- [ ] **Step 2: 实现 `scripts/voice_videos.sh`**

结构逐行照搬 `pure_videos.sh`：循环 `VOICE_MUSICS_DIR/*/`；`ls "$dir"/*.wav | wc -l` 校验唯一 wav、`[ -f "$dir/lyrics.txt" ]`；调 `render_player.py --song-wav "$WAV" --lyrics-txt "$dir/lyrics.txt" --title "$STEM" --artist "$ARTIST" --timing "$WORK/timing.json" --output "$WORK/silent_9x16.mp4"`；ffmpeg 合流 `-i silent -i "$WAV" -c:v copy -c:a aac -b:a 192k -shortest -movflags +faststart`；`mv` 到 OUT_DIR 加 `_player_9x16` 后缀；失败 echo FAILED 置 FAIL=1，继续下一首。不做 `mv "$WAV"` 归档（spec）。

- [ ] **Step 3: Makefile 目标**

```make
voice-videos:
	bash scripts/voice_videos.sh
```

并加入 `.PHONY`；`check` 目标的 `bash -n` 列表追加 `scripts/voice_videos.sh`。

- [ ] **Step 4: README 章节**

对照 spec「流水线规格」节写：目录约定（`voice-musics/<歌名>/` 一 wav + lyrics.txt）、逐首步骤、缓存与失效（mtime 对比 wav+lyrics）、覆盖率验收门、命名 `<歌名>_player_9x16.mp4`、环境变量表、运行 `make voice-videos`。

- [ ] **Step 5: 验证**

```bash
bash -n scripts/voice_videos.sh
uv run python -m compileall -q scripts tests
uv run python -m unittest discover -s tests
make voice-videos    # 真实集成: 处理 voice-musics/ 两首歌
```

预期两首均 OK；`ffprobe` 抽查成片 ≥ 数十秒、有音轨；不同歌背景色相不同（人眼/抽帧对比）。

- [ ] **Step 6: Commit** `feat: voice_videos.sh batch pipeline, make target and docs`

---

## Self-Review

- **Spec 覆盖**：布局（T3/T4）、确定性配色（T3）、逐字 karaoke（T2/T4）、音符粒子（T4/T5）、进度条（T4）、缓存与失效（T5）、覆盖率验收门（T2/T5）、批处理与命名（T6）、文档（T6）——全覆盖；16:9 已按用户指示从 spec 移除，无残留任务。
- **类型一致**：`tokens_from_alignment` 在 T2 测试与实现间以测试为准；`compute_timing_tokens(song_path, lines, language)` 被 T5 消费，签名一致；`NoteParticles.update` 返回 `(sprite, x, y, alpha)` 元组列表，T4 的 `note_sprites()` 提供其输入。
- **Review Focus 归属**：1→T1，2→T5 CacheTests，3→T2 CoverageTests+T5 退出码，4→T2 TokenTests，5→T4 LyricZoneTests。

