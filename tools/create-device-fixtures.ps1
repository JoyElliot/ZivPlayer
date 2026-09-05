# SPDX-License-Identifier: GPL-3.0-or-later

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$fixtureRoot = Join-Path $repositoryRoot 'native/out/device-fixtures'
$ffmpegPath = (Get-Command ffmpeg -ErrorAction Stop).Source
$ffprobePath = (Get-Command ffprobe -ErrorAction Stop).Source
[void](New-Item -ItemType Directory -Path $fixtureRoot -Force)

function Invoke-FixtureFfmpeg {
    param([string[]]$Arguments)
    & $ffmpegPath -nostdin -hide_banner -loglevel error -y @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "FFmpeg fixture generation failed with exit code $LASTEXITCODE."
    }
}

$assPath = Join-Path $fixtureRoot 'external.ass'
$srtPath = Join-Path $fixtureRoot 'external.srt'
$utf8 = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText($assPath, @'
[Script Info]
Title: ZivPlayer generated subtitle fixture
ScriptType: v4.00+
PlayResX: 640
PlayResY: 360
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,sans-serif,30,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,20,20,25,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.50,0:00:04.00,Default,,0,0,0,,ASS subtitle - ZivPlayer
Dialogue: 0,0:00:04.00,0:00:08.00,Default,,0,0,0,,{\c&H00FFFF&\b1}中文 ASS 字幕{\b0}\NOutline and two-line layout
Dialogue: 0,0:00:08.00,0:00:11.50,Default,,0,0,0,,{\move(100,80,540,80)}Moving subtitle
'@.Replace("`r`n", "`n") + "`n", $utf8)
[System.IO.File]::WriteAllText($srtPath, @'
1
00:00:00,500 --> 00:00:04,000
SRT subtitle - ZivPlayer

2
00:00:04,000 --> 00:00:08,000
中文 SRT 字幕
Second subtitle line

3
00:00:08,000 --> 00:00:11,500
Subtitle end and replay test
'@.Replace("`r`n", "`n") + "`n", $utf8)

$mp4Path = Join-Path $fixtureRoot 'baseline-av.mp4'
Invoke-FixtureFfmpeg -Arguments @(
    '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24:duration=12',
    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=12',
    '-map', '0:v:0', '-map', '1:a:0',
    '-c:v', 'libx264', '-threads:v', '1', '-preset', 'veryfast', '-crf', '24',
    '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '96k', '-ac', '2',
    '-metadata:s:a:0', 'title=440 Hz', '-movflags', '+faststart', $mp4Path
)

$mkvPath = Join-Path $fixtureRoot 'tracks-subtitles.mkv'
Invoke-FixtureFfmpeg -Arguments @(
    '-i', $mp4Path,
    '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000:duration=12',
    '-i', $assPath, '-i', $srtPath,
    '-map', '0:v:0', '-map', '0:a:0', '-map', '1:a:0', '-map', '2:0', '-map', '3:0',
    '-c:v', 'copy', '-c:a:0', 'copy', '-c:a:1', 'aac', '-b:a:1', '96k',
    '-ac:a:1', '2', '-c:s', 'copy', '-t', '12',
    '-metadata:s:a:0', 'language=eng', '-metadata:s:a:0', 'title=440 Hz',
    '-metadata:s:a:1', 'language=jpn', '-metadata:s:a:1', 'title=880 Hz',
    '-metadata:s:s:0', 'language=zho', '-metadata:s:s:0', 'title=ASS embedded',
    '-metadata:s:s:1', 'language=eng', '-metadata:s:s:1', 'title=SRT embedded',
    '-disposition:a:0', 'default', '-disposition:a:1', '0',
    '-disposition:s:0', 'default', '-disposition:s:1', '0', $mkvPath
)

$entries = foreach ($fixturePath in @($mp4Path, $mkvPath, $assPath, $srtPath)) {
    $item = Get-Item -LiteralPath $fixturePath
    [ordered]@{
        file = $item.Name
        sizeBytes = $item.Length
        sha256 = (Get-FileHash -LiteralPath $fixturePath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
$probeText = & $ffprobePath -v error -show_streams -show_format -of json $mkvPath
if ($LASTEXITCODE -ne 0) { throw 'FFprobe fixture inspection failed.' }
$probe = $probeText -join "`n" | ConvertFrom-Json
$streamKinds = @($probe.streams | ForEach-Object { $_.codec_type })
$streamCodecs = @($probe.streams | ForEach-Object { $_.codec_name })
if (($streamKinds -join ',') -ne 'video,audio,audio,subtitle,subtitle' -or
    ($streamCodecs -join ',') -ne 'h264,aac,aac,ass,subrip') {
    throw "Unexpected fixture streams: $($streamKinds -join ',') / $($streamCodecs -join ',')."
}
$manifest = [ordered]@{
    schemaVersion = 1
    kind = 'zivplayer-device-fixtures'
    content = 'Self-generated test pattern, 440/880 Hz tones, and original subtitles; no third-party media.'
    ffmpegVersion = (& $ffmpegPath -version | Select-Object -First 1)
    files = @($entries)
    matroskaStreams = @($probe.streams | Select-Object index, codec_name, codec_type, tags)
}
[System.IO.File]::WriteAllText(
    (Join-Path $fixtureRoot 'manifest.json'),
    ($manifest | ConvertTo-Json -Depth 8) + "`n",
    $utf8
)
Write-Output "Created and inspected device fixtures: $fixtureRoot"
