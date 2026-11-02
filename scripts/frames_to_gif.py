#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 demo_record.mjs 录下的帧合成 GIF（不依赖 ffmpeg，只用 Pillow）

为什么自己写而不是用 ffmpeg/ImageMagick：
- 本机没有 ffmpeg/ImageMagick，装一个只为合成 GIF 太重；
- Pillow 已可用，且能按"每帧真实时长"控制 GIF 节奏，比固定 fps 更贴近真实体验。

运行：
    python3 scripts/frames_to_gif.py
    python3 scripts/frames_to_gif.py --frames /tmp/demo_frames --out docs/demo.gif --width 900
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

DEFAULT_FRAMES = Path("/tmp/demo_frames")
DEFAULT_OUT = Path("docs/demo.gif")



def dedupe(frames, durations, threshold=0.8):
    """合并"画面几乎没变"的连续帧：把时长累加到前一帧。

    比"均匀抽帧"好：打字/滚动这类有变化的帧全部保留，静止等待的帧被压缩。
    """
    from PIL import ImageChops, ImageStat

    keep = [frames[0]]
    delays = [durations[0]]
    prev = frames[0].convert("L").resize((96, 60))
    for image, delay in zip(frames[1:], durations[1:]):
        cur = image.convert("L").resize((96, 60))
        diff = ImageStat.Stat(ImageChops.difference(prev, cur)).mean[0]
        if diff < threshold:
            delays[-1] = min(delays[-1] + delay, 1500)
        else:
            keep.append(image)
            delays.append(delay)
        prev = cur
    return keep, delays


def main() -> int:
    parser = argparse.ArgumentParser(description="帧序列 → GIF")
    parser.add_argument("--frames", default=str(DEFAULT_FRAMES))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--width", type=int, default=900, help="输出宽度（等比缩放）")
    parser.add_argument("--max-mb", type=float, default=4.5, help="超过该体积则降色/抽帧重试")
    parser.add_argument("--diff-threshold", type=float, default=0.8, help="帧去重阈值（越小保留越多帧）")
    args = parser.parse_args()

    frame_dir = Path(args.frames)
    meta_file = frame_dir / "meta.json"
    if not meta_file.exists():
        raise SystemExit(f"找不到 {meta_file}，先运行 node scripts/demo_record.mjs")

    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    if not meta:
        raise SystemExit("帧为空")

    images: list[Image.Image] = []
    durations: list[int] = []
    for item in meta:
        path = frame_dir / item["name"]
        if not path.exists():
            continue
        image = Image.open(path).convert("RGB")
        if image.width != args.width:
            height = round(image.height * args.width / image.width)
            image = image.resize((args.width, height), Image.LANCZOS)
        # 量化到 128 色，显著减小体积；GIF 本身也只有 256 色上限
        images.append(image.quantize(colors=128, method=Image.MEDIANCUT))
        durations.append(int(item.get("durationMs") or 120))

    if not images:
        raise SystemExit("没有可用帧")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    def save(frames: list[Image.Image], delays: list[int], colors: int) -> int:
        converted = [f.quantize(colors=colors) if f.palette is None else f for f in frames]
        converted[0].save(
            out,
            save_all=True,
            append_images=converted[1:],
            duration=delays,
            loop=0,
            optimize=True,
            disposal=2,
        )
        return out.stat().st_size

    images, durations = dedupe(images, durations, threshold=args.diff_threshold)
    print(f"  去重后 {len(images)} 帧（原 {len(meta)} 帧，静止段已合并）")

    # 降级顺序：先降色（对 UI 截图影响小）→ 再抽帧（影响流畅度）
    plans = [
        (128, 1),
        (64, 1),
        (32, 1),
        (32, 2),   # 每 2 帧留 1 帧
        (24, 3),   # 每 3 帧留 1 帧
        (16, 4),
    ]
    frames_out, delays_out, size, used_plan = images, durations, 0, plans[0]
    for colors, step in plans:
        if step == 1:
            frames_out, delays_out = images, durations
        else:
            frames_out = images[::step]
            delays_out = [
                min(sum(durations[i:i + step]), 600) for i in range(0, len(durations), step)
            ]
        size = save(frames_out, delays_out, colors)
        used_plan = (colors, step)
        print(f"  尝试 {colors} 色 / 每 {step} 帧留 1 → {len(frames_out)} 帧，{size / 1024 / 1024:.2f} MB")
        if size <= args.max_mb * 1024 * 1024:
            break

    print(f"✅ 已生成 {out}（{len(frames_out)} 帧 / {used_plan[0]} 色，{size / 1024 / 1024:.2f} MB，"
          f"{sum(durations) / 1000:.1f} 秒）")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
