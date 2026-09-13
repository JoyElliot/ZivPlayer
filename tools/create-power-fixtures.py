#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Original matched-content steady-playback clips; no personal media or downloads."""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('native/out/power-fixtures'))
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    ffmpeg, ffprobe = (shutil.which(name) for name in ('ffmpeg', 'ffprobe'))
    if not ffmpeg or not ffprobe:
        raise SystemExit('ffmpeg and ffprobe must be available')
    variants = [
        ('avc', 'libx264', 'yuv420p', ['-preset', 'fast', '-x264-params', 'keyint=96:min-keyint=96:scenecut=0']),
        ('hevc', 'libx265', 'yuv420p', ['-preset', 'fast', '-x265-params', 'keyint=96:min-keyint=96:scenecut=0:pools=4:frame-threads=2:log-level=error']),
        ('hevc10', 'libx265', 'yuv420p10le', ['-preset', 'fast', '-x265-params', 'keyint=96:min-keyint=96:scenecut=0:pools=4:frame-threads=2:log-level=error']),
        ('vp9', 'libvpx-vp9', 'yuv420p', ['-deadline', 'good', '-cpu-used', '4', '-row-mt', '1', '-g', '96']),
        ('av1', 'libsvtav1', 'yuv420p', ['-preset', '10', '-svtav1-params', 'keyint=96:scd=0:lp=4']),
    ]
    audio = out / 'common-audio.m4a'
    if not audio.exists():
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=360',
            '-ac', '2', '-c:a', 'aac', '-b:a', '128k', str(audio)], check=True)
    def generate(variant):
        name, encoder, pixel_format, extra = variant
        segment = out / (name + '-segment.mkv')
        target = out / (name + '-1080p24.mkv')
        log_path = out / (name + '-generation.log')
        with log_path.open('w') as log:
            if not segment.exists():
                subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'warning', '-nostdin', '-n',
                    '-f', 'lavfi', '-i', 'testsrc2=size=1920x1080:rate=24:duration=30',
                    '-an', '-c:v', encoder, '-pix_fmt', pixel_format, '-threads', '4',
                    '-b:v', '6M', *extra, str(segment)], check=True, stdout=log, stderr=log)
            if not target.exists():
                # Stream-copy repetitions form one continuous timeline, with no player loop or seek.
                subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'warning', '-nostdin', '-n',
                    '-stream_loop', '11', '-i', str(segment), '-i', str(audio),
                    '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy', '-t', '360', str(target)],
                    check=True, stdout=log, stderr=log)
        details = json.loads(subprocess.check_output([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(target)]))
        video = next(stream for stream in details['streams'] if stream['codec_type'] == 'video')
        sound = next(stream for stream in details['streams'] if stream['codec_type'] == 'audio')
        assert (video['width'], video['height'], video['avg_frame_rate']) == (1920, 1080, '24/1')
        assert video['pix_fmt'] == pixel_format and sound['codec_name'] == 'aac'
        assert sound['sample_rate'] == '48000' and sound['channels'] == 2
        assert 359.5 <= float(details['format']['duration']) <= 360.5
        row = {'id': name, 'file': target.name, 'sha256': hashlib.file_digest(target.open('rb'), 'sha256').hexdigest(),
               'bytes': target.stat().st_size, 'encoder': encoder, 'encoderArguments': extra, 'ffprobe': details}
        print(f'{name}: {target.stat().st_size} bytes verified', flush=True)
        return row
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(generate, variants))
    manifest = {'source': 'Original deterministic lavfi testsrc2; all variants encoded from same graph',
                'width': 1920, 'height': 1080, 'fps': 24, 'durationSeconds': 360,
                'sourceCycleSeconds': 30, 'targetVideoBitrate': 6000000,
                'audio': 'Identical copied AAC LC stereo 48000 Hz stream, 128 kbit/s target; output is muted during test',
                'ffmpegVersion': subprocess.check_output([ffmpeg, '-version']).decode().splitlines()[0], 'files': rows}
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
if __name__ == '__main__':
    main()
