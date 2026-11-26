"""Turn frames from scripts/record_demo.js into the README GIF and an MP4 (needs ffmpeg).

    python scripts/build_demo.py demo-frames               # -> examples/demo.gif, demo.mp4
    python scripts/build_demo.py demo-frames --mp4-only out.mp4

Frames come from Chrome's screencast with real timestamps, so the output keeps the real
timing. Frames shorter than 60 ms are merged for the GIF (browsers slow very short GIF
delays down to 100 ms).
"""
import argparse
import json
import subprocess
from pathlib import Path


def concat_list(frames: Path, min_dur: float) -> Path:
    meta = json.loads((frames / "meta.json").read_text())
    out = []
    for (name, t), (_, t_next) in zip(meta, meta[1:]):
        d = t_next - t
        if out and out[-1][1] < min_dur:
            out[-1] = [name, out[-1][1] + d]
        else:
            out.append([name, d])
    lst = frames / f"list-{int(min_dur * 1000)}.txt"
    lst.write_text("".join(f"file '{n}'\nduration {max(d, min_dur):.4f}\n" for n, d in out)
                   + f"file '{out[-1][0]}'\n")
    return lst


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("frames", type=Path)
    ap.add_argument("--gif", type=Path, default=Path("examples/demo.gif"))
    ap.add_argument("--mp4", type=Path, default=Path("demo.mp4"))
    ap.add_argument("--mp4-only", action="store_true")
    a = ap.parse_args()
    frames = a.frames.resolve()
    ffmpeg("-f", "concat", "-safe", "0", "-i", str(concat_list(frames, 0.0)),
           "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-crf", "22", "-preset", "slow",
           "-tune", "stillimage", "-movflags", "+faststart", str(a.mp4.resolve()))
    if not a.mp4_only:
        ffmpeg("-f", "concat", "-safe", "0", "-i", str(concat_list(frames, 0.06)),
               "-vf", "scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];"
                      "[b][p]paletteuse=dither=none:diff_mode=rectangle",
               "-fps_mode", "vfr", str(a.gif.resolve()))


if __name__ == "__main__":
    main()
