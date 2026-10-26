#!/system/bin/sh
# Keep preparation, repair and completed phases awake without touching measured legs.
set -u
BASE=$1
case "$BASE" in /data/local/tmp/ziv-power-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]/awake-guard) ;; *) exit 64 ;; esac
PARENT=${BASE%/awake-guard}
. "$BASE/config.env"
case "$HARD_STOP_EPOCH:$ORIGINAL_TIMEOUT" in *[!0-9:]*|:*|*::*|*:) exit 64 ;; esac
timed() { /system/bin/toybox timeout -k 2 15 "$@"; }
utc() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
mkdir "$BASE/active.lock" || exit 73
trap 'rmdir "$BASE/active.lock" 2>/dev/null || :' EXIT
printf '%s\n' "$$" > "$BASE/pid"
cat /proc/$$/stat > "$BASE/process-stat.txt"
timed settings put system screen_off_timeout 86400000 || exit 70
printf '{"readyUtc":"%s","pid":%s,"screenTimeoutMs":86400000,"samplingTouches":false}\n' "$(utc)" "$$" > "$BASE/ready.json"
while [ ! -f "$BASE/stop" ]; do
    [ "$HARD_STOP_EPOCH" -eq 0 ] || [ "$(date +%s)" -lt "$HARD_STOP_EPOCH" ] || break
    active=$(cat "$BASE/active-run.txt" 2>/dev/null) || active=
    case "$active" in "$PARENT"/*) ;; *) active= ;; esac
    case "$active" in *..*) active= ;; esac
    phase=
    [ -z "$active" ] || phase=$(awk -F '\t' '{print $2}' "$active/status.tsv" 2>/dev/null)
    # A runner owns its entire stage, including cooling and setup. Serialize idle
    # screen maintenance with launch so no stale phase read can touch playback.
    if { [ -z "$active" ] || [ -f "$active/exit.json" ]; } && ln -s "$BASE/process-stat.txt" "$PARENT/transition.lock" 2>/dev/null; then
        active=$(cat "$BASE/active-run.txt" 2>/dev/null) || active=
        if { [ -z "$active" ] || [ -f "$active/exit.json" ]; } && [ ! -d "$PARENT/night-guard/recovery-intent.lock" ]; then
        timed settings put system screen_off_timeout 86400000 || :
        if timed dumpsys power > "$BASE/power.txt" &&
           ! grep -Eq '^[[:space:]]*mWakefulness=Awake[[:space:]]*$' "$BASE/power.txt"; then
            printf '%s\twake_recovery\n' "$(utc)" >> "$BASE/events.tsv"
            timed input keyevent 224 || :
        fi
        if timed dumpsys window policy > "$BASE/policy.txt" &&
           grep -Eq '^[[:space:]]*showing=true[[:space:]]*$' "$BASE/policy.txt"; then
            printf '%s\tkeyguard_detected\n' "$(utc)" >> "$BASE/events.tsv"
            timed wm dismiss-keyguard || :
        fi
        fi
        [ "$(readlink "$PARENT/transition.lock")" != "$BASE/process-stat.txt" ] || rm "$PARENT/transition.lock"
    fi
    printf '%s\t%s\n' "$(utc)" "${phase:-preparation_or_idle}" > "$BASE/heartbeat.tsv"
    sleep 30
done
# Return the real pre-experiment timeout; never force screen-off or lock.
ok=true
timed settings put system screen_off_timeout "$ORIGINAL_TIMEOUT" || ok=false
[ "$(timed settings get system screen_off_timeout)" = "$ORIGINAL_TIMEOUT" ] || ok=false
printf '{"finishedUtc":"%s","screenTimeoutMs":%s,"cleanupSucceeded":%s}\n' "$(utc)" "$ORIGINAL_TIMEOUT" "$ok" > "$BASE/exit.json"
[ "$ok" = true ]
