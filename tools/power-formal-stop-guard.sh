#!/system/bin/sh
# Independent device-local stop, twenty seconds before the declared sampling limit.
set -u
BASE=$1
case "$BASE" in /data/local/tmp/ziv-power-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]/formal-stop) ;; *) exit 64 ;; esac
PARENT=${BASE%/formal-stop}
. "$BASE/config.env"
case "$HARD_STOP_EPOCH" in ''|*[!0-9]*) exit 64 ;; esac
utc() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
start_id() { fields=${1##*) }; set -- $fields; [ "$#" -ge 20 ] || return 1; shift 19; printf '%s' "$1"; }
finish() { printf '{"finishedUtc":"%s","action":"%s","ok":%s}\n' "$(utc)" "$1" "$2" > "$BASE/exit.json"; }
mkdir "$BASE/active.lock" || exit 73
trap 'rmdir "$BASE/active.lock" 2>/dev/null || :' EXIT
printf '%s\n' "$$" > "$BASE/pid"
cat /proc/$$/stat > "$BASE/process-stat.txt"
printf '{"pid":%s,"readyUtc":"%s"}\n' "$$" "$(utc)" > "$BASE/ready.json"
while :; do
    if [ -f "$PARENT/night-guard/deadline-fired.txt" ] || [ -f "$PARENT/night-guard/exit.json" ]; then
        finish recovery_started true; exit 0
    fi
    remaining=$((HARD_STOP_EPOCH-$(date +%s)))
    [ "$remaining" -gt 0 ] || break
    [ "$remaining" -le 30 ] || remaining=30
    sleep "$remaining"
done
[ "$(cat "$PARENT/night-guard/owner.txt")" = "$OWNER" ] || { finish owner_mismatch false; exit 1; }
active=$(cat "$PARENT/night-guard/active-run.txt" 2>/dev/null) || active=
case "$active" in "$PARENT"/*) ;; *) finish no_active_stage true; exit 0 ;; esac
case "$active" in *..*) finish invalid_active_stage false; exit 1 ;; esac
saved=$(cat "$active/runner-process-stat.txt" 2>/dev/null) || { finish missing_runner_identity false; exit 1; }
pid=${saved%% *}
case "$pid" in ''|*[!0-9]*) finish invalid_runner_identity false; exit 1 ;; esac
live=$(cat "/proc/$pid/stat" 2>/dev/null) || { finish runner_already_exited true; exit 0; }
[ "$(start_id "$saved")" = "$(start_id "$live")" ] || { finish former_runner_exited true; exit 0; }
fields=${live##*) }; set -- $fields
[ "$3" = "$pid" ] && [ "$4" = "$pid" ] || { finish group_mismatch false; exit 1; }
[ "$(awk '/^Uid:/ {print $2}' /proc/$pid/status)" = 2000 ] || { finish uid_mismatch false; exit 1; }
[ "$(tr '\000' ' ' < /proc/$pid/cmdline)" = "sh $active/runner.sh $active " ] || { finish command_mismatch false; exit 1; }
printf 'night_deadline\n' > "$active/deadline-cancel.tmp" || exit 1
mv "$active/deadline-cancel.tmp" "$active/cancel" || exit 1
# Compare the immutable install path whose experiment SHA was verified during
# preparation; do not put a large APK hash read on the deadline critical path.
path=$(/system/bin/toybox timeout -k 1 3 pm path io.github.joyelliot.zivplayer) || { finish package_probe_failed false; exit 1; }
expected_path=$(cat "$BASE/experiment-apk-path.txt") || { finish expected_package_path_missing false; exit 1; }
if [ "$path" = "$expected_path" ] && [ ! -f "$PARENT/night-guard/deadline-fired.txt" ]; then
    /system/bin/toybox timeout -k 1 5 am force-stop io.github.joyelliot.zivplayer || { finish force_stop_failed false; exit 1; }
fi
finish formal_stop_requested true
