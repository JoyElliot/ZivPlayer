# SPDX-License-Identifier: GPL-3.0-or-later
<#
Samples an already-running debug app. Does not install, launch, stop, reset counters,
change settings, or operate playback. Gfxinfo describes Android UI frames, not mpv video FPS.
Battery/thermal snapshots are context, not a calibrated per-app power measurement.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^[a-zA-Z0-9._:-]+$')][string]$Serial,
    [ValidateRange(10,3600)][int]$DurationSeconds = 60,
    [ValidateRange(2,60)][int]$IntervalSeconds = 5
)
$ErrorActionPreference = 'Stop'
if ([Math]::Ceiling($DurationSeconds / $IntervalSeconds) -gt 360) { throw 'Select at most 360 samples per run.' }
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$adbPath = (Get-Command adb -ErrorAction Stop).Source
$package = 'io.github.joyelliot.zivplayer'
$outputRoot = Join-Path $repositoryRoot ('native/out/device-runtime-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'))
[void](New-Item -ItemType Directory -Path $outputRoot)
$utf8 = [Text.UTF8Encoding]::new($false)

function Invoke-AdbRead {
    param([string[]]$Arguments)
    $startInfo = [Diagnostics.ProcessStartInfo]::new($adbPath)
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in (@('-s', $Serial) + $Arguments)) { $startInfo.ArgumentList.Add($argument) }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (!$process.WaitForExit(10000)) {
            $process.Kill()
            return [pscustomobject]@{ exitCode=-1; text='ADB read timed out.' }
        }
        $text = $stdout.GetAwaiter().GetResult() + $stderr.GetAwaiter().GetResult()
        if ($text.Length -gt 1MB) { $text = $text.Substring(0, 1MB) + "`n[truncated]" }
        return [pscustomobject]@{ exitCode=$process.ExitCode; text=$text }
    } finally { $process.Dispose() }
}

function Save-AdbRead {
    param([string]$Name, [string[]]$Arguments)
    $result = Invoke-AdbRead -Arguments $Arguments
    [IO.File]::WriteAllText((Join-Path $outputRoot $Name), $result.text, $utf8)
    return $result.exitCode
}

$state = Invoke-AdbRead -Arguments @('get-state')
if ($state.exitCode -ne 0 -or $state.text.Trim() -ne 'device') { throw 'The selected device is not available.' }
$environment = [ordered]@{
    api = (Invoke-AdbRead -Arguments @('shell','getprop','ro.build.version.sdk')).text.Trim()
    model = (Invoke-AdbRead -Arguments @('shell','getprop','ro.product.model')).text.Trim()
    requestedDurationSeconds = $DurationSeconds
    intervalSeconds = $IntervalSeconds
    scope = 'Read-only snapshots of an already-running app. No launch, install, playback control, settings changes or counter reset.'
}
[void](Save-AdbRead -Name 'battery-before.txt' -Arguments @('shell','dumpsys','battery'))
[void](Save-AdbRead -Name 'thermal-before.txt' -Arguments @('shell','dumpsys','thermalservice'))
$timer = [Diagnostics.Stopwatch]::StartNew()
$samples = [Collections.Generic.List[object]]::new()
do {
    $index = $samples.Count
    $pidResult = Invoke-AdbRead -Arguments @('shell','pidof',$package)
    $appPid = if ($pidResult.exitCode -eq 0 -and $pidResult.text.Trim() -match '^\d+$') { $pidResult.text.Trim() } else { $null }
    $fdCount = $null
    if ($appPid) {
        $fds = Invoke-AdbRead -Arguments @('shell','run-as',$package,'ls',"/proc/$appPid/fd")
        if ($fds.exitCode -eq 0) { $fdCount = @($fds.text -split '\s+' | Where-Object { $_ -match '^\d+$' }).Count }
    }
    $memoryStatus = Save-AdbRead -Name ('memory-{0:D3}.txt' -f $index) -Arguments @('shell','dumpsys','meminfo',$package)
    $samples.Add([ordered]@{ elapsedSeconds=[Math]::Round($timer.Elapsed.TotalSeconds,3); observedAtUtc=[DateTime]::UtcNow.ToString('o');
        processId=$appPid; fileDescriptorCount=$fdCount; memoryDumpExitCode=$memoryStatus })
    $remaining = $DurationSeconds - $timer.Elapsed.TotalSeconds
    if ($remaining -gt 0) { Start-Sleep -Milliseconds ([int]([Math]::Min($IntervalSeconds,$remaining) * 1000)) }
} while ($timer.Elapsed.TotalSeconds -lt $DurationSeconds)
[void](Save-AdbRead -Name 'battery-after.txt' -Arguments @('shell','dumpsys','battery'))
[void](Save-AdbRead -Name 'thermal-after.txt' -Arguments @('shell','dumpsys','thermalservice'))
[void](Save-AdbRead -Name 'ui-frames.txt' -Arguments @('shell','dumpsys','gfxinfo',$package,'framestats'))
$report = [ordered]@{ schemaVersion=1; kind='zivplayer-device-runtime-observation'; environment=$environment;
    elapsedSeconds=[Math]::Round($timer.Elapsed.TotalSeconds,3); samples=@($samples.ToArray());
    interpretation='No automatic pass/fail. A missing/denied measurement remains null. Native FPS/drop/decoder data must be exported from the app diagnostics page.' }
[IO.File]::WriteAllText((Join-Path $outputRoot 'manifest.json'), ($report | ConvertTo-Json -Depth 6) + "`n", $utf8)
Write-Output "Captured $($samples.Count) samples: $outputRoot"
