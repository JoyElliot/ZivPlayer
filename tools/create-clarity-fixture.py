#!/usr/bin/env python3
"""Create a static SDR luma target; every video frame has the same identity."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw, ImageFont


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1920, 1080), (128, 128, 128))
    draw = ImageDraw.Draw(image)
    font_path = Path("C:/Windows/Fonts/consola.ttf")
    for size, y in ((36, 36), (24, 100), (18, 148), (12, 185)):
        font = ImageFont.truetype(str(font_path), size)
        draw.text((64, y), "ZivPlayer 1080p clarity target | ABC abc 0123456789 /\\ +-=", font=font, fill=(224,)*3)
    rois = []
    for index, stripe in enumerate((1, 2, 3, 4, 6, 8)):
        x0 = 64 + 296 * index
        for horizontal, y0 in ((False, 300), (True, 580)):
            for offset in range(240):
                level = 48 if (offset // stripe) % 2 == 0 else 208
                rect = (x0, y0 + offset, x0 + 239, y0 + offset) if horizontal else (x0 + offset, y0, x0 + offset, y0 + 239)
                draw.line(rect, fill=(level,)*3)
            label = f"{'H' if horizontal else 'V'} {stripe}px"
            draw.text((x0, y0 - 35), label, font=ImageFont.truetype(str(font_path), 20), fill=(224,)*3)
            rois.append({"name": label.replace(" ", "_"), "rect": [x0 + 24, y0 + 24, x0 + 216, y0 + 216], "stripeWidth": stripe})
    for index, level in enumerate((32, 48, 64, 96, 128, 160, 192, 208, 224)):
        x0 = 64 + index * 112
        draw.rectangle((x0, 900, x0 + 79, 1019), fill=(level,)*3)
        rois.append({"name": f"gray_{level}", "rect": [x0 + 10, 920, x0 + 70, 1000], "level": level})
    draw.rectangle((1200, 890, 1799, 1019), fill=(48,)*3)
    draw.polygon(((1445, 890), (1799, 890), (1799, 1019), (1457, 1019)), fill=(208,)*3)
    rois.append({"name": "slanted_edge", "rect": [1380, 900, 1530, 1010]})
    draw.rectangle((0, 0, 1919, 1079), outline=(224,)*3, width=4)
    png = args.output / "clarity-target.png"
    video = args.output / "clarity-target.mp4"
    image.save(png)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", "-loop", "1", "-i", str(png),
                    "-t", "12", "-r", "24", "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "8", "-g", "24", "-bf", "0",
                    "-color_range", "tv", "-colorspace", "bt709", "-color_trc", "bt709", "-color_primaries", "bt709",
                    "-an", "-movflags", "+faststart", str(video)], check=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", "-ss", "2", "-i", str(video),
                    "-frames:v", "1", "-update", "1", str(args.output / "decoded-reference.png")], check=True)
    manifest = {"size": [1920, 1080], "staticInputImage": True, "decodedReferencePositionMs": 2000,
                "encodingNote": "Lossy encoding can change a few decoded values between GOPs; verify decoded frame hashes for the selected capture interval.", "rois": rois,
                "videoSha256": hashlib.sha256(video.read_bytes()).hexdigest(), "videoBytes": video.stat().st_size}
    (args.output / "fixture.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k != "rois"}))


if __name__ == "__main__":
    main()
