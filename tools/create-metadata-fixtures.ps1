# SPDX-License-Identifier: GPL-3.0-or-later
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$fixtureRoot = Join-Path (Split-Path -Parent $PSScriptRoot) 'native/out/device-fixtures'
[void](New-Item -ItemType Directory -Path $fixtureRoot -Force)

# Original, deterministic artwork: no network or personal media is used.
$coverPath = Join-Path $fixtureRoot 'metadata-cover.jpg'
& ffmpeg -nostdin -hide_banner -loglevel error -y -f lavfi -i 'color=c=0x2457c5:size=256x256,drawbox=x=24:y=24:w=96:h=208:color=0xf4bb44:t=fill,drawbox=x=136:y=96:w=96:h=136:color=0x45b99a:t=fill' -frames:v 1 -update 1 $coverPath
if ($LASTEXITCODE -ne 0) { throw 'Metadata cover generation failed.' }

& ffmpeg -nostdin -hide_banner -loglevel error -y -f lavfi -i 'sine=frequency=440:sample_rate=48000:duration=8' -i $coverPath -map 0:a -map 1:v -c:a libmp3lame -b:a 128k -ac 2 -c:v copy -id3v2_version 3 -metadata title='Evening Test' -metadata artist='ZivPlayer Artist' -metadata album='Original Test Album' -metadata album_artist='ZivPlayer Ensemble' -metadata date='2026' -metadata genre='Test' -metadata track='3/8' -metadata:s:v title='Front cover' -metadata:s:v comment='Cover (front)' (Join-Path $fixtureRoot 'metadata-music.mp3')
if ($LASTEXITCODE -ne 0) { throw 'Tagged music generation failed.' }

& ffmpeg -nostdin -hide_banner -loglevel error -y -f lavfi -i 'testsrc2=size=640x360:rate=24:duration=8' -f lavfi -i 'sine=frequency=660:sample_rate=48000:duration=8' -c:v libx264 -threads:v 2 -preset veryfast -crf 25 -pix_fmt yuv420p -c:a aac -b:a 96k -ac 2 -metadata title='Original Video Sample' -metadata artist='ZivPlayer Studio' -metadata date='2026' -movflags +faststart (Join-Path $fixtureRoot 'metadata-video.mp4')
if ($LASTEXITCODE -ne 0) { throw 'Tagged video generation failed.' }
Write-Output 'Created original tagged music, video, and embedded artwork fixtures.'
