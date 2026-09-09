# SPDX-License-Identifier: GPL-3.0-or-later
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$fixtureRoot = Join-Path (Split-Path -Parent $PSScriptRoot) 'native/out/device-fixtures'
[void](New-Item -ItemType Directory -Path $fixtureRoot -Force)
# Twelve binary cells encode the displayed frame, independently of optimistic player position.
$filters = @('drawbox=x=0:y=260:w=1280:h=200:color=black:t=fill')
for ($bit = 0; $bit -lt 12; $bit++) {
    $x = 64 + 88 * $bit
    $divisor = [int][Math]::Pow(2, $bit)
    $filters += "drawbox=x=${x}:y=280:w=56:h=160:color=white:t=fill:enable='mod(floor(n/$divisor),2)'"
}
$filters += 'drawbox=x=1152:y=280:w=64:h=160:color=white:t=fill'
& ffmpeg -nostdin -hide_banner -loglevel error -y -f lavfi -i 'testsrc2=size=1280x720:rate=24:duration=90' -f lavfi -i 'sine=frequency=440:sample_rate=48000:duration=90' -vf ($filters -join ',') -c:v libx264 -threads:v 2 -preset veryfast -crf 24 -g 96 -keyint_min 96 -sc_threshold 0 -pix_fmt yuv420p -c:a aac -b:a 96k -ac 2 -movflags +faststart (Join-Path $fixtureRoot 'seek-preview.mp4')
if ($LASTEXITCODE -ne 0) { throw 'Seek fixture generation failed.' }
Write-Output "Created 90 s / 24 fps / 4 s GOP frame-coded fixture in $fixtureRoot"
