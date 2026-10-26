#!/usr/bin/env python3
"""Run one bounded, fresh-process capture and retain its raw device evidence."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import time

PACKAGE = "io.github.joyelliot.zivplayer"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--engine", choices=("exo", "libmpv", "vlc"), required=True)
    parser.add_argument("--buffer", choices=("default", "source", "screen"), required=True)
    parser.add_argument("--fixture", default="clarity-target.mp4")
    parser.add_argument("--position-ms", type=int, default=2000)
    parser.add_argument("--start-position-ms", type=int, default=0)
    parser.add_argument("--decoder-mode", choices=("automatic", "software"), default="automatic")
    parser.add_argument("--regressions", action="store_true")
    parser.add_argument("--page-transitions", action="store_true")
    parser.add_argument("--subtitles", action="store_true")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--background-play-ms", type=int, default=600)
    parser.add_argument("--background-observe-ms", type=int, default=0)
    parser.add_argument("--background-seek", action="store_true")
    parser.add_argument("--transient-surface", action="store_true")
    parser.add_argument("--subtitle-preroll", action="store_true")
    parser.add_argument("--play-to-position", action="store_true")
    parser.add_argument("--initial-text-index", type=int, choices=(-1, 0, 1), default=-1)
    parser.add_argument("--tracks-during-restore", action="store_true")
    parser.add_argument("--restore-play-pause", action="store_true")
    parser.add_argument("--geometry-spec", type=Path)
    args = parser.parse_args()
    assert re.fullmatch(r"[a-z0-9-]{1,80}", args.label)
    assert re.fullmatch(r"[a-z0-9-]+\.(mp4|mkv)", args.fixture)
    assert 0 <= args.position_ms <= 60000
    assert 0 <= args.start_position_ms <= 60000
    local_fixture = args.output / "fixture" / args.fixture
    fixture_sha256 = hashlib.sha256(local_fixture.read_bytes()).hexdigest()
    destination = args.output / args.label
    destination.mkdir(parents=True, exist_ok=False)
    base = ["adb", "-s", args.serial]
    def adb(*commands, timeout=30):
        return subprocess.check_output(base + list(commands), timeout=timeout)
    remote_sha256 = adb("shell", "run-as", PACKAGE, "sha256sum", "files/power-fixtures/" + args.fixture).decode().split()[0]
    assert remote_sha256 == fixture_sha256, "Stage the exact local fixture before running a capture."
    installed = {}
    for package in (PACKAGE, PACKAGE + ".test"):
        paths = adb("shell", "pm", "path", package).decode().splitlines()
        assert len(paths) == 1 and paths[0].startswith("package:/data/app/") and paths[0].endswith("/base.apk")
        installed[package] = adb("shell", "sha256sum", paths[0][8:]).decode().split()[0]
    (destination / "battery-before.txt").write_bytes(adb("shell", "dumpsys", "battery"))
    adb("shell", "am", "force-stop", PACKAGE)
    adb("shell", "am", "force-stop", PACKAGE + ".test")
    if args.subtitles:
        # Starting instrumentation clears only the target package's stopped flag.
        # Explicitly activate the generated-fixture provider before the app uses it.
        assert b"external.ass" in adb("shell", "content", "query", "--uri",
            "content://" + PACKAGE + ".test.fixtures/external.ass")
    command = base + ["shell", "am", "instrument", "-w", "-r",
                      "-e", "class", PACKAGE + ".ClarityCaptureTest",
                      "-e", "clarityEngine", args.engine, "-e", "clarityBuffer", args.buffer,
                      "-e", "clarityLabel", args.label, "-e", "clarityFixture", args.fixture,
                      "-e", "clarityDecoderMode", args.decoder_mode, "-e", "clarityFixtureSha256", fixture_sha256,
                      "-e", "clarityRegressions", str(args.regressions).lower(),
                      "-e", "clarityPageTransitions", str(args.page_transitions).lower(),
                      "-e", "claritySubtitles", str(args.subtitles).lower(),
                      "-e", "clarityBackground", str(args.background).lower(),
                      "-e", "clarityBackgroundPlayMs", str(args.background_play_ms),
                      "-e", "clarityBackgroundObserveMs", str(args.background_observe_ms),
                      "-e", "clarityBackgroundSeek", str(args.background_seek).lower(),
                      "-e", "clarityTransientSurface", str(args.transient_surface).lower(),
                      "-e", "claritySubtitlePreroll", str(args.subtitle_preroll).lower(),
                      "-e", "clarityPlayToPosition", str(args.play_to_position).lower(),
                      "-e", "clarityInitialTextIndex", str(args.initial_text_index),
                      "-e", "clarityTracksDuringRestore", str(args.tracks_during_restore).lower(),
                      "-e", "clarityRestorePlayPause", str(args.restore_play_pause).lower(),
                      "-e", "clarityPositionMs", str(args.position_ms),
                      "-e", "clarityStartPositionMs", str(args.start_position_ms),
                      PACKAGE + ".test/androidx.test.runner.AndroidJUnitRunner"]
    if args.geometry_spec:
        geometry = json.loads(args.geometry_spec.read_text(encoding="utf-8"))
        assert all(isinstance(value, int) for value in geometry.values())
        command[-1:-1] = ["-e", "clarityGeometry", "'" + json.dumps(geometry, separators=(",", ":")) + "'"]
    started = time.time()
    native_log = (destination / "native-live-logcat.txt").open("wb")
    logger = subprocess.Popen(base + ["logcat", "-v", "threadtime", "-T", "1", "VLC:D", "*:S"],
                              stdout=native_log, stderr=subprocess.STDOUT)
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        output = result.stdout
    except subprocess.TimeoutExpired as error:
        output = error.stdout or b""
        (destination / "instrumentation.log").write_bytes(output)
        adb("shell", "am", "force-stop", PACKAGE)
        raise
    finally:
        logger.terminate()
        logger.wait(timeout=10)
        native_log.close()
    (destination / "instrumentation.log").write_bytes(output)
    # SurfaceFlinger snapshots can make a completed run tens of MiB. A slow
    # wireless evidence transfer must not be confused with playback failure.
    archive = adb("exec-out", "run-as", PACKAGE, "tar", "cf", "-", "files/clarity-results/" + args.label, timeout=120)
    (destination / "device.tar").write_bytes(archive)
    members = []
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for item in tar:
            if not item.isfile():
                continue
            relative = Path(item.name).relative_to("files/clarity-results/" + args.label)
            assert len(relative.parts) == 1 and relative.name not in (".", "..")
            data = tar.extractfile(item).read()
            (destination / relative.name).write_bytes(data)
            members.append({"name": relative.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    success = result.returncode == 0 and b"OK (1 test)" in output and b"INSTRUMENTATION_CODE: -1" in output
    receipt = {"label": args.label, "engine": args.engine, "buffer": args.buffer,
               "requestedDecoderMode": args.decoder_mode, "expectedFixtureSha256": fixture_sha256,
               "installedApkSha256": installed,
               "elapsedSeconds": time.time() - started, "exitCode": result.returncode, "success": success,
               "archiveSha256": hashlib.sha256(archive).hexdigest(), "files": members}
    (destination / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in receipt.items() if k != "files"}))
    print(output.decode("utf-8", errors="replace")[-1400:])
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
