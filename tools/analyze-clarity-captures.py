#!/usr/bin/env python3
"""Measure fixed luma line pairs in unresized screen crops; retain all raw evidence."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def luma(image):
    rgb = np.asarray(image.convert("RGB"), dtype=np.float64)
    return rgb @ np.array([0.2126, 0.7152, 0.0722])


def region(image, roi):
    x0, y0, x1, y1 = roi["rect"]
    return image[y0:y1, x0:x1]


def metrics(image, reference, rois):
    gray = {r["level"]: float(region(image, r).mean()) for r in rois if "level" in r}
    swing = gray[208] - gray[48]
    assert swing > 50, "The static target is missing or obscured."
    reference_gray = {r["level"]: float(region(reference, r).mean()) for r in rois if "level" in r}
    reference_swing = reference_gray[208] - reference_gray[48]
    stripes = {}
    for roi in rois:
        if "stripeWidth" not in roi:
            continue
        ref = region(reference, roi)
        patch = region(image, roi)
        high = ref > (reference_gray[208] + reference_gray[48]) / 2
        low = ~high
        difference = float(patch[high].mean() - patch[low].mean())
        reference_modulation = float(ref[high].mean() - ref[low].mean()) / reference_swing
        stripes[roi["name"]] = {"normalizedModulation": difference / swing / reference_modulation,
                                "brightMean": float(patch[high].mean()), "darkMean": float(patch[low].mean())}
    # Each row crosses the same gently slanted dark/light edge. Interpolate crossings
    # locally; this is a descriptive 10-90% edge width, not a standards-certified MTF.
    edge_widths = []
    for y in range(905, 1005):
        profile = (image[y, 1425:1480] - gray[48]) / swing
        crossings = []
        for threshold in (0.1, 0.9):
            index = np.flatnonzero(profile >= threshold)
            if len(index) == 0 or index[0] == 0:
                break
            i = index[0]
            crossings.append(i - 1 + (threshold - profile[i - 1]) / (profile[i] - profile[i - 1]))
        if len(crossings) == 2:
            edge_widths.append(crossings[1] - crossings[0])
    return {"grayPatchMeans": gray, "flatPatchSwing": swing, "linePairs": stripes,
            "edgeWidth10to90MedianPixels": float(np.median(edge_widths)) if edge_widths else None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    fixture = json.loads((args.root / "fixture/fixture.json").read_text(encoding="utf-8"))
    reference = luma(Image.open(args.root / "fixture/decoded-reference.png"))
    references = {"clarity-target.mp4": reference}
    fixture_hashes = {"clarity-target.mp4": fixture["videoSha256"]}
    tagged = args.root / "fixture/clarity-target-tagged.mp4"
    if tagged.exists():
        references[tagged.name] = luma(Image.open(args.root / "fixture/decoded-reference-tagged.png"))
        fixture_hashes[tagged.name] = hashlib.sha256(tagged.read_bytes()).hexdigest()
    rows = []
    comparison = []
    for path in sorted(args.root.glob("*/capture.json")):
        capture = json.loads(path.read_text(encoding="utf-8"))
        if not capture.get("completed") or capture["fixture"] not in references:
            continue
        assert capture["fixtureSha256"] == fixture_hashes[capture["fixture"]]
        capture_reference = references[capture["fixture"]]
        assert capture["cleanupSucceeded"]
        geometry = capture["geometry"]
        assert (geometry["viewWidth"], geometry["viewHeight"]) == (1920, 1080)
        crop = (geometry["screenX"], geometry["screenY"], geometry["screenX"] + 1920, geometry["screenY"] + 1080)
        samples = []
        screen_arrays = []
        raw_arrays = []
        for index in range(3):
            screen = Image.open(path.parent / f"screen-{index}.png").crop(crop)
            raw_path = path.parent / f"raw-{index}.png"
            raw = Image.open(raw_path) if raw_path.exists() else None
            if raw is None:
                assert capture.get("nativeCapturePath") == "vlc-surface-control-child-window-readback"
            else:
                assert raw.size == (geometry["bufferWidth"], geometry["bufferHeight"])
            screen_arrays.append(np.asarray(screen.convert("RGB")))
            if raw is not None:
                raw_arrays.append(np.asarray(raw.convert("RGB")))
            sample = {"screen": metrics(luma(screen), capture_reference, fixture["rois"])}
            if raw is not None and raw.size == (1920, 1080):
                sample["raw"] = metrics(luma(raw), capture_reference, fixture["rois"])
            samples.append(sample)
            if index == 1:
                screen.save(path.parent / "screen-video-crop.png")
                comparison.append((capture["label"], screen.copy()))
        rows.append({"label": capture["label"], "engine": capture["requestedEngine"], "policy": capture["bufferPolicy"],
                     "fixture": capture["fixture"], "requestedDecoderMode": capture.get("requestedDecoderMode", "automatic"),
                     "geometry": geometry, "diagnostics": capture["diagnostics"],
                     "allScreensPixelIdentical": all(np.array_equal(screen_arrays[0], a) for a in screen_arrays[1:]),
                     "nativeCapturePath": capture.get("nativeCapturePath", "surface-pixel-copy"),
                     "allRawPixelIdentical": all(np.array_equal(raw_arrays[0], a) for a in raw_arrays[1:]) if raw_arrays else None, "samples": samples})
    result = {"method": "Unresized 1920x1080 screen ROI; line-pair bright-minus-dark amplitude normalized by the same capture's 48/208 flat gray patches and decoded reference. Signed modulation requires exact alignment. Exo's padded decoder buffers need a separate screenshot-transform audit.",
              "fixtureHashes": fixture_hashes, "reference": metrics(reference, reference, fixture["rois"]), "runs": rows}
    (args.root / "analysis.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    for row in rows:
        metric = row["samples"][1]["screen"]
        print(row["label"], "stable", row["allScreensPixelIdentical"], "edge", round(metric["edgeWidth10to90MedianPixels"], 3),
              "V1/V2/H1/H2", [round(metric["linePairs"][k]["normalizedModulation"], 4) for k in ("V_1px", "V_2px", "H_1px", "H_2px")])
    # A literal crop comparison, enlarged by nearest-neighbor only for readable
    # display; all measurements above use original pixels.
    selected = {label: image for label, image in comparison}
    order = [s for s in ("vlc-default-01", "vlc-source-01", "mpv-default-01", "mpv-source-01") if s in selected]
    if order:
        panel = Image.new("RGB", (1400, 290 * len(order)), "#17191e")
        draw = ImageDraw.Draw(panel)
        font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 24)
        for index, label in enumerate(order):
            y = index * 290
            draw.text((20, y + 12), label + " | original screenshot pixels, 2x nearest", font=font, fill="white")
            patch = selected[label].crop((56, 130, 736, 245)).resize((1360, 230), Image.Resampling.NEAREST)
            panel.paste(patch, (20, y + 50))
        panel.save(args.root / "text-comparison.png")


if __name__ == "__main__":
    main()
