# SPDX-License-Identifier: GPL-3.0-or-later
[CmdletBinding()]
param([switch]$VerifyOnly)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$fixtureRoot = Join-Path $repositoryRoot 'native/out/format-matrix'
$ffmpegPath = (Get-Command ffmpeg -ErrorAction Stop).Source
$ffprobePath = (Get-Command ffprobe -ErrorAction Stop).Source
[void](New-Item -ItemType Directory -Path $fixtureRoot -Force)
$utf8 = [System.Text.UTF8Encoding]::new($false)
$tone = @('-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=5')
$pattern = @('-f', 'lavfi', '-i', 'testsrc2=size=1920x1080:rate=30:duration=5')
$videoMaps = @('-map', '0:v:0', '-map', '1:a:0', '-ac', '2', '-t', '5')
$hevc = @('-c:v', 'libx265', '-preset', 'ultrafast', '-crf', '25', '-pix_fmt', 'yuv420p10le', '-x265-params', 'pools=2:frame-threads=1')
$aac = @('-c:a', 'aac', '-b:a', '128k')
$entries = @(
    @{ name='avc-1080p50mbps.mp4'; video='h264'; profile='High'; pixel='yuv420p'; audio='aac'; width=1920; height=1080;
       args=$pattern + $tone + $videoMaps + @('-c:v','libx264','-preset','veryfast','-profile:v','high','-level:v','4.2','-pix_fmt','yuv420p','-b:v','50M','-minrate','50M','-maxrate','50M','-bufsize','100M','-x264-params','nal-hrd=cbr:filler=1') + $aac + @('-movflags','+faststart') },
    @{ name='hevc-main10-flac.mkv'; video='hevc'; profile='Main 10'; pixel='yuv420p10le'; audio='flac'; width=1920; height=1080;
       args=$pattern + $tone + $videoMaps + $hevc + @('-c:a','flac','-sample_fmt','s16') },
    @{ name='av1-opus.webm'; video='av1'; profile='Main'; pixel='yuv420p'; audio='opus'; width=1920; height=1080;
       args=$pattern + $tone + $videoMaps + @('-c:v','libsvtav1','-preset','8','-crf','30','-pix_fmt','yuv420p','-svtav1-params','lp=2','-c:a','libopus','-b:a','96k') },
    @{ name='hevc-2160p30.mkv'; video='hevc'; profile='Main 10'; pixel='yuv420p10le'; audio='aac'; width=3840; height=2160;
       args=@('-f','lavfi','-i','testsrc2=size=3840x2160:rate=30:duration=3') + $tone + @('-map','0:v:0','-map','1:a:0','-ac','2','-t','3') + $hevc + $aac }
)

# Generate real PQ/HLG samples from a normalized floating-point linear ramp. These
# are transfer/color-space test signals, not mastering/calibration reference images.
foreach ($transfer in @('smpte2084','arib-std-b67')) {
    $label = if ($transfer -eq 'smpte2084') { 'pq' } else { 'hlg' }
    $graph = "format=gbrpf32le,geq=r='X/W':g='Y/H':b='(X+Y)/(W+H)',zscale=tin=linear:pin=bt709:min=gbr:rin=full:t=${transfer}:p=bt2020:m=2020_ncl:r=limited:npl=1000,format=yuv420p10le"
    $entries += @{ name="hdr-$label-main10.mkv"; video='hevc'; profile='Main 10'; pixel='yuv420p10le'; audio='aac'; width=1920; height=1080; transfer=$transfer;
        args=@('-f','lavfi','-i','color=c=black:size=1920x1080:rate=30:duration=5') + $tone + $videoMaps + @('-vf',$graph) + $hevc + $aac + @('-color_primaries','bt2020','-color_trc',$transfer,'-colorspace','bt2020nc','-color_range','tv') }
}
foreach ($audio in @(
    @{name='audio-opus.opus'; codec='opus'; args=@('-c:a','libopus','-b:a','128k')},
    @{name='audio-flac.flac'; codec='flac'; args=@('-c:a','flac','-sample_fmt','s16')},
    @{name='audio-pcm.wav'; codec='pcm_s16le'; args=@('-c:a','pcm_s16le')}
)) {
    $entries += @{ name=$audio.name; audio=$audio.codec; args=$tone + @('-map','0:a:0','-ac','2','-t','4') + $audio.args }
}

$files = foreach ($entry in $entries) {
    $path = Join-Path $fixtureRoot $entry.name
    $logPath = Join-Path $fixtureRoot ($entry.name + '.encode.log')
    $arguments = $entry.args
    if (!$VerifyOnly) {
        & $ffmpegPath -nostdin -hide_banner -loglevel warning -y -filter_threads 1 @arguments -threads:v 2 $path *> $logPath
        if ($LASTEXITCODE -ne 0) { throw "Encoding failed: $($entry.name); inspect $logPath" }
    }
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing sample: $path" }
    $probeText = & $ffprobePath -v error -show_streams -show_format -of json $path
    if ($LASTEXITCODE -ne 0) { throw "FFprobe failed: $($entry.name)" }
    $probe = $probeText -join "`n" | ConvertFrom-Json
    $video = @($probe.streams | Where-Object codec_type -eq 'video')
    $audio = @($probe.streams | Where-Object codec_type -eq 'audio')
    if ($audio.Count -ne 1 -or $audio[0].codec_name -ne $entry.audio -or $audio[0].channels -ne 2 -or $audio[0].sample_rate -ne '48000') { throw "Audio mismatch: $($entry.name)" }
    if ($entry.video) {
        if ($video.Count -ne 1 -or $video[0].codec_name -ne $entry.video -or $video[0].profile -ne $entry.profile -or
            $video[0].pix_fmt -ne $entry.pixel -or $video[0].width -ne $entry.width -or $video[0].height -ne $entry.height -or $video[0].r_frame_rate -ne '30/1') { throw "Video mismatch: $($entry.name)" }
    } elseif ($video.Count -ne 0) { throw "Unexpected video: $($entry.name)" }
    if ($entry.transfer -and ($video[0].color_transfer -ne $entry.transfer -or $video[0].color_primaries -ne 'bt2020' -or $video[0].color_space -ne 'bt2020nc' -or $video[0].color_range -ne 'tv')) { throw "HDR signal tags mismatch: $($entry.name)" }
    $duration = [double]::Parse($probe.format.duration, [Globalization.CultureInfo]::InvariantCulture)
    if ($duration -lt 2.9 -or $duration -gt 5.2) { throw "Unexpected duration: $($entry.name)" }
    & $ffmpegPath -nostdin -v error -threads 2 -i $path -f null - *> (Join-Path $fixtureRoot ($entry.name + '.decode.log'))
    if ($LASTEXITCODE -ne 0) { throw "Full software decode failed: $($entry.name)" }
    $signal = $null
    if ($entry.transfer) {
        $signalLog = Join-Path $fixtureRoot ($entry.name + '.signal.log')
        & $ffmpegPath -nostdin -hide_banner -loglevel info -threads 2 -i $path -map 0:v:0 -vf 'signalstats,metadata=print' -frames:v 1 -an -f null - *> $signalLog
        if ($LASTEXITCODE -ne 0) { throw "HDR signal inspection failed: $($entry.name)" }
        $signalText = Get-Content -LiteralPath $signalLog -Raw
        $yMin = [double][regex]::Match($signalText, 'lavfi.signalstats.YMIN=(\d+(?:\.\d+)?)').Groups[1].Value
        $yMax = [double][regex]::Match($signalText, 'lavfi.signalstats.YMAX=(\d+(?:\.\d+)?)').Groups[1].Value
        # Lossy block coding raises the single black corner of the two-dimensional ramp.
        if ($yMin -gt 200 -or $yMin -lt 40 -or $yMax -lt 650 -or $yMax -gt 965) { throw "HDR ramp signal range mismatch: $($entry.name) [$yMin,$yMax]" }
        if ($entry.transfer -eq 'smpte2084' -and $yMax -gt 750) { throw 'PQ nominal peak exceeds the intended 1000-nit test range.' }
        $signal = [ordered]@{ yMin=$yMin; yMax=$yMax; bitDepth=10; nominalLinearPeak=1;
            nominalPeakNits=$(if ($entry.transfer -eq 'smpte2084') { 1000 } else { $null }); masteringMetadata='omitted; this is not a calibration source' }
    }
    $item = Get-Item -LiteralPath $path
    [ordered]@{ file=$item.Name; sizeBytes=$item.Length; sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant();
        durationSeconds=$duration; streams=@($probe.streams | Select-Object codec_type,codec_name,profile,pix_fmt,width,height,r_frame_rate,sample_rate,channels,color_range,color_space,color_transfer,color_primaries,bit_rate); signal=$signal; fullSoftwareDecode='passed' }
}
$totalBytes = [long]0
foreach ($file in $files) { $totalBytes += [long]$file.sizeBytes }
if ($totalBytes -gt 250MB) { throw "Fixture budget exceeded: $totalBytes bytes" }
$manifest = [ordered]@{ schemaVersion=1; kind='zivplayer-format-matrix'; generatedAtUtc=[DateTime]::UtcNow.ToString('o');
    content='Original lavfi test patterns and sine tones. Host encoding, ffprobe and full software decode only; Android playback remains untested.';
    ffmpegVersion=(& $ffmpegPath -version | Select-Object -First 1); totalBytes=$totalBytes; files=@($files) }
[IO.File]::WriteAllText((Join-Path $fixtureRoot 'manifest.json'), ($manifest | ConvertTo-Json -Depth 8) + "`n", $utf8)
Write-Output "Created and verified $($files.Count) optional format samples ($totalBytes bytes): $fixtureRoot"
