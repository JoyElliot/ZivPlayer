#!/system/bin/sh
# Device-local open-ended recovery: explicit request, runner failure, or orphaned handoff.
set -u
BASE=$1
case "$BASE" in /data/local/tmp/ziv-power-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]/night-guard) ;; *) exit 64 ;; esac
. "$BASE/config.env"
KEEP_AWAKE_ON_EXIT=${KEEP_AWAKE_ON_EXIT:-false}
REQUIRE_FULL_RECOVERY=${REQUIRE_FULL_RECOVERY:-false}
case "$KEEP_AWAKE_ON_EXIT:$REQUIRE_FULL_RECOVERY" in true:true|true:false|false:true|false:false) ;; *) exit 64 ;; esac
PACKAGE=io.github.joyelliot.zivplayer
PARENT=${BASE%/night-guard}
[ "$KEEP_AWAKE_ON_EXIT" = true ] || exit 64
RESTORE_ORIGINAL_TIMEOUT=${RESTORE_ORIGINAL_TIMEOUT:-false}
RECOVERY_TRIGGER=waiting
guard_sleep_pid=
guard_sleep_stat=
timed() { /system/bin/toybox timeout -k 2 8 "$@"; }
utc() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
owner_package_state() { awk '/^Packages:/ {packages=1; next} packages && /^[[:space:]]*User 0:.*installed=true/ {print; exit}' "$1"; }
stat_start_id() { fields=${1##*) }; set -- $fields; [ "$#" -ge 20 ] || return 1; shift 19; printf '%s' "$1"; }
uptime_s() { read uptime_value uptime_rest < /proc/uptime; printf '%s' "${uptime_value%%.*}"; }
watch_active_runner() {
    watched_active=$1
    watched_now=$2
    if [ "${watched_directory:-}" != "$watched_active" ]; then
        watched_directory=$watched_active
        watched_stage_since=$watched_now
        watched_signature=
        watched_phase_since=$watched_now
    fi
    next_signature=$(cut -f2-4 "$watched_active/status.tsv" 2>/dev/null) || next_signature=
    if [ "$next_signature" != "$watched_signature" ]; then
        watched_signature=$next_signature
        watched_phase_since=$watched_now
    fi
    watched_phase=$(printf '%s' "$watched_signature" | cut -f1)
    case "$watched_phase" in
        cooling|restoring|finished) watched_phase_budget=1800 ;;
        running) watched_phase_budget=1200 ;;
        *) watched_phase_budget=900 ;;
    esac
    watched_rows=$(wc -l < "$watched_active/manifest.tsv" 2>/dev/null) || watched_rows=4
    watched_rows=$(printf '%s' "$watched_rows" | tr -d '[:space:]')
    case "$watched_rows" in 1|2|3|4|5|6|7|8) ;; *) watched_rows=4 ;; esac
    if [ "$((watched_now-watched_phase_since))" -ge "$watched_phase_budget" ] ||
       [ "$((watched_now-watched_stage_since))" -ge "$((watched_rows*3000+900))" ]; then
        RECOVERY_TRIGGER=runner_progress_timeout
        return 1
    fi
    return 0
}
child_identity_evidence_ok() {
    evidence_active=$1
    # A clean early exit can precede the network child. An unexpected disappearance
    # cannot use a missing identity as proof that the fork never happened.
    if [ ! -f "$evidence_active/network-process-stat.txt" ] &&
       ! grep -q '"cleanupSucceeded":true' "$evidence_active/exit.json" 2>/dev/null; then
        return 1
    fi
    # running is published before instrumentation forks, including its narrow
    # fork-to-stat window. Do not install APKs without evidence in that case.
    evidence_label=$(awk -F '\t' '$2=="running" {label=$4} END {print label}' "$evidence_active/events.tsv" 2>/dev/null) || evidence_label=
    if [ -n "$evidence_label" ]; then
        case "$evidence_label" in *[!A-Za-z0-9_-]*) return 1 ;; esac
        # The active file survives between legs. It must match the latest leg's
        # own record, otherwise a new fork may still lack its published identity.
        [ -f "$evidence_active/active-am-process-stat.txt" ] &&
        [ -f "$evidence_active/control/$evidence_label.am-process-stat.txt" ] &&
        cmp -s "$evidence_active/active-am-process-stat.txt" "$evidence_active/control/$evidence_label.am-process-stat.txt" || return 1
    fi
    return 0
}
stop_guard_sleep() {
    [ -n "$guard_sleep_pid" ] && [ -n "$guard_sleep_stat" ] || return 0
    guard_sleep_live=$(cat "/proc/$guard_sleep_pid/stat" 2>/dev/null) || guard_sleep_live=
    if [ -n "$guard_sleep_live" ] && [ "$(stat_start_id "$guard_sleep_live")" = "$(stat_start_id "$guard_sleep_stat")" ]; then
        guard_sleep_fields=${guard_sleep_live##*) }; set -- $guard_sleep_fields
        [ "$2" != "$$" ] || kill -TERM "$guard_sleep_pid" 2>/dev/null || :
    fi
}
request_recovery() { RECOVERY_TRIGGER=host_requested; stop_guard_sleep; }
trap request_recovery USR1
wait_until_recovery() {
    wait_seconds=$1
    [ "$wait_seconds" -gt 0 ] && [ "$RECOVERY_TRIGGER" = waiting ] || return 0
    sleep "$wait_seconds" &
    guard_sleep_pid=$!
    guard_sleep_stat=$(cat "/proc/$guard_sleep_pid/stat" 2>/dev/null) || guard_sleep_stat=
    # The trap also stops this exact child, including a signal just before wait.
    [ "$RECOVERY_TRIGGER" = waiting ] || stop_guard_sleep
    wait "$guard_sleep_pid" 2>/dev/null || :
    [ "$RECOVERY_TRIGGER" = waiting ] || stop_guard_sleep
    wait "$guard_sleep_pid" 2>/dev/null || :
    guard_sleep_pid=
    guard_sleep_stat=
}
case "$HARD_STOP_EPOCH:$ORIGINAL_TIMEOUT:$ORIGINAL_BT:$ORIGINAL_BRIGHTNESS:$ORIGINAL_BRIGHTNESS_MODE" in *[!0-9:]*|:*|*::*|*:) exit 64 ;; esac
[ -n "$ORIGINAL_TEST_SHA" ] && [ -n "$MAIN_SHA" ] || exit 64
if [ "$REQUIRE_FULL_RECOVERY" = true ]; then
    [ -n "${RESTORE_MAIN_APK:-}" ] && [ -n "${RESTORE_MAIN_SHA:-}" ] &&
    [ -n "${ORIGINAL_MUSIC_VOLUME:-}" ] && [ -f "$BASE/background-packages.txt" ] || exit 64
    [ -f "$RESTORE_MAIN_APK" ] && [ -f "$BASE/original-test.apk" ] || exit 64
fi
if [ "${REQUIRE_BACKGROUND_JOURNALS:-false}" = true ]; then
    [ -f "$BASE/stopped-packages.txt" ] && [ -f "$BASE/enabled-packages.tsv" ] || exit 64
fi
stop_recorded_group() {
    recorded_file=$1
    recorded=$(cat "$recorded_file" 2>/dev/null) || return 0
    recorded_pid=${recorded%% *}
    case "$recorded_pid" in ''|*[!0-9]*) return 1 ;; esac
    [ "$recorded_pid" != "$$" ] || return 1
    recorded_fields=${recorded##*) }; set -- $recorded_fields
    [ "$#" -ge 20 ] && [ "$3" = "$recorded_pid" ] && [ "$4" = "$recorded_pid" ] || return 1
    start_id() { fields=${1##*) }; set -- $fields; [ "$#" -ge 20 ] || return 1; shift 19; printf '%s' "$1"; }
    live=$(cat "/proc/$recorded_pid/stat" 2>/dev/null) || { ! kill -0 -- "-$recorded_pid" 2>/dev/null; return $?; }
    [ "$(start_id "$recorded")" = "$(start_id "$live")" ] || { ! kill -0 -- "-$recorded_pid" 2>/dev/null; return $?; }
    fields=${live##*) }; set -- $fields
    [ "$3" = "$recorded_pid" ] && [ "$4" = "$recorded_pid" ] || return 1
    [ "$(awk '/^Uid:/ {print $2}' /proc/$recorded_pid/status)" = 2000 ] || return 1
    command=$(tr '\000' ' ' < /proc/$recorded_pid/cmdline)
    case "$recorded_file" in
        */runner-process-stat.txt) [ "$command" = "sh ${recorded_file%/runner-process-stat.txt}/runner.sh ${recorded_file%/runner-process-stat.txt} " ] || return 1 ;;
        */active-am-process-stat.txt) case "$command" in '/system/bin/toybox timeout --foreground '*am\ instrument*io.github.joyelliot.zivplayer.test*) ;; *) return 1 ;; esac ;;
        */network-process-stat.txt) [ "$command" = 'ip -ts monitor link dev wlan0 ' ] || return 1 ;;
        *) return 1 ;;
    esac
    kill -TERM -- "-$recorded_pid" 2>/dev/null || return 1
    n=0
    while kill -0 "$recorded_pid" 2>/dev/null && [ "$n" -lt 30 ]; do sleep 1; n=$((n+1)); done
    live=$(cat "/proc/$recorded_pid/stat" 2>/dev/null) || { ! kill -0 -- "-$recorded_pid" 2>/dev/null; return $?; }
    if [ "$(start_id "$recorded")" = "$(start_id "$live")" ]; then
        kill -KILL -- "-$recorded_pid" 2>/dev/null || return 1
        sleep 1
    fi
    live=$(cat "/proc/$recorded_pid/stat" 2>/dev/null) || { ! kill -0 -- "-$recorded_pid" 2>/dev/null; return $?; }
    [ "$(start_id "$recorded")" != "$(start_id "$live")" ] && ! kill -0 -- "-$recorded_pid" 2>/dev/null
}
stop_awake_guard() {
    awake="$PARENT/awake-guard"
    if [ ! -f "$awake/owner.txt" ] && [ ! -f "$awake/process-stat.txt" ] && [ ! -f "$awake/ready.json" ]; then return 0; fi
    [ "$(cat "$awake/owner.txt")" = "${AWAKE_GUARD_OWNER:-missing}" ] || return 1
    saved=$(cat "$awake/process-stat.txt" 2>/dev/null) || { [ ! -f "$awake/ready.json" ]; return $?; }
    pid=${saved%% *}
    case "$pid" in ''|*[!0-9]*) return 1 ;; esac
    [ "$pid" != "$$" ] || return 1
    live=$(cat "/proc/$pid/stat" 2>/dev/null) || return 0
    [ "$(stat_start_id "$saved")" = "$(stat_start_id "$live")" ] || return 0
    fields=${live##*) }; set -- $fields
    [ "$3" = "$pid" ] && [ "$4" = "$pid" ] || return 1
    uid=$(awk '/^Uid:/ {print $2}' "/proc/$pid/status")
    [ "$uid" = 2000 ] || return 1
    command=$(tr '\000' ' ' < "/proc/$pid/cmdline")
    [ "$command" = "sh $awake/guard.sh $awake " ] || return 1
    : > "$awake/stop" || return 1
    attempt=0
    while [ "$attempt" -lt 90 ]; do
        live=$(cat "/proc/$pid/stat" 2>/dev/null) || return 0
        [ "$(stat_start_id "$saved")" = "$(stat_start_id "$live")" ] || return 0
        fields=${live##*) }; set -- $fields
        [ "$1" != Z ] || return 0
        attempt=$((attempt+1)); sleep 1
    done
    return 1
}
mkdir "$BASE/active-v2.lock" || exit 73
trap 'rmdir "$BASE/active-v2.lock" 2>/dev/null || :' EXIT
printf '%s\n' "$$" > "$BASE/guard-v2.pid"
cat /proc/$$/stat > "$BASE/guard-v2-process-stat.txt"
printf '{"version":2,"pid":%s,"readyUtc":"%s"}\n' "$$" "$(utc)" > "$BASE/guard-v2-ready.tmp"
mv "$BASE/guard-v2-ready.tmp" "$BASE/guard-v2-ready.json"
# No wall-clock completion deadline. Read only owned local evidence while sampling.
last_active=
idle_since=$(date +%s)
while [ "$RECOVERY_TRIGGER" = waiting ]; do
    if [ -f "$PARENT/stop-request.txt" ]; then RECOVERY_TRIGGER=user_requested; break; fi
    if [ -f "$PARENT/awake-guard/ready.json" ]; then
        awake_saved=$(cat "$PARENT/awake-guard/process-stat.txt" 2>/dev/null) || awake_saved=
        awake_pid=${awake_saved%% *}
        case "$awake_pid" in ''|*[!0-9]*) RECOVERY_TRIGGER=awake_identity_missing; break ;; esac
        awake_live=$(cat "/proc/$awake_pid/stat" 2>/dev/null) || awake_live=
        if [ -z "$awake_live" ] || [ "$(stat_start_id "$awake_saved")" != "$(stat_start_id "$awake_live")" ]; then RECOVERY_TRIGGER=awake_lost; break; fi
        awake_fields=${awake_live##*) }; set -- $awake_fields
        if [ "$1" = Z ]; then RECOVERY_TRIGGER=awake_lost; break; fi
    fi
    active=$(cat "$BASE/active-run.txt" 2>/dev/null) || active=
    case "$active" in "$PARENT"/*) ;; *) active= ;; esac
    case "$active" in *..*) active= ;; esac
    now=$(date +%s)
    if [ -n "$active" ] && [ "$active" != "$last_active" ]; then
        last_active=$active; idle_since=$now
    fi
    if [ -n "$active" ] && [ -f "$active/started.json" ]; then
        if [ -f "$active/exit.json" ]; then
            if ! grep -q '\"stopReason\":\"manifest_completed\"' "$active/exit.json" ||
               ! grep -q '\"cleanupSucceeded\":true' "$active/exit.json"; then
                RECOVERY_TRIGGER=runner_stopped; break
            fi
        else
            saved=$(cat "$active/runner-process-stat.txt" 2>/dev/null) || saved=
            runner_pid=${saved%% *}
            case "$runner_pid" in ''|*[!0-9]*) RECOVERY_TRIGGER=runner_identity_missing; break ;; esac
            live=$(cat "/proc/$runner_pid/stat" 2>/dev/null) || live=
            if [ -z "$live" ] || [ "$(stat_start_id "$saved")" != "$(stat_start_id "$live")" ]; then
                RECOVERY_TRIGGER=runner_lost; break
            fi
            runner_fields=${live##*) }; set -- $runner_fields
            if [ "$1" = Z ]; then RECOVERY_TRIGGER=runner_lost; break; fi
            watch_active_runner "$active" "$(uptime_s)" || break
            idle_since=$now
        fi
    fi
    # An abandoned preparation or inter-stage handoff is a fault, not a scheduled end.
    if [ "$((now-idle_since))" -ge "${IDLE_RECOVERY_SECONDS:-1800}" ]; then
        RECOVERY_TRIGGER=orphaned_handoff; break
    fi
    wait_until_recovery 15
    # wait_until_recovery is signal-aware and uses only a sleeping child.
done
mkdir "$BASE/recovery-intent.lock" || exit 70
printf '%s\n' "$RECOVERY_TRIGGER" > "$BASE/recovery-intent" || :
# The device-local launch transaction holds this lock only until runner identity exists.
lock_tries=0
until ln -s "$BASE/guard-v2-process-stat.txt" "$PARENT/transition.lock" 2>/dev/null; do
    lock_tries=$((lock_tries+1))
    lock_target=$(readlink "$PARENT/transition.lock" 2>/dev/null) || lock_target=
    case "$lock_target" in "$PARENT"/*/process-stat.txt|"$PARENT"/*/launch-process-stat.txt|"$PARENT"/night-guard/guard-v2-process-stat.txt) ;; *) lock_target= ;; esac
    case "$lock_target" in *..*) lock_target= ;; esac
    lock_saved=
    [ -z "$lock_target" ] || lock_saved=$(cat "$lock_target" 2>/dev/null) || lock_saved=
    lock_pid=${lock_saved%% *}
    case "$lock_pid" in ''|*[!0-9]*) lock_pid= ;; esac
    if [ -n "$lock_pid" ]; then
        lock_live=$(cat "/proc/$lock_pid/stat" 2>/dev/null) || lock_live=
        lock_dead=false
        if [ -z "$lock_live" ] || [ "$(stat_start_id "$lock_saved")" != "$(stat_start_id "$lock_live")" ]; then lock_dead=true
        else
            lock_fields=${lock_live##*) }; set -- $lock_fields
            [ "$1" != Z ] || lock_dead=true
        fi
        if [ "$lock_dead" = true ] && [ "$(readlink "$PARENT/transition.lock")" = "$lock_target" ]; then
            rm "$PARENT/transition.lock"
            continue
        fi
    fi
    [ "$lock_tries" -lt 120 ] || printf '%s\n' transition_lock_still_owned > "$BASE/termination-warning.txt"
    sleep 1
done
trap 'rm -f "$PARENT/transition.lock"; rmdir "$BASE/active-v2.lock" 2>/dev/null || :' EXIT
if [ "$REQUIRE_FULL_RECOVERY" != true ] && [ -f "$BASE/night-complete" ]; then
    printf '{"finishedUtc":"%s","action":"already_completed"}\n' "$(utc)" > "$BASE/exit.json"
    exit 0
fi
active=$(cat "$BASE/active-run.txt" 2>/dev/null) || active=
case "$active" in "$PARENT"/*) ;; *) active= ;; esac
case "$active" in *..*) active= ;; esac
if [ "$REQUIRE_FULL_RECOVERY" != true ] && [ -n "$active" ] && [ -f "$BASE/final-stage.started" ] && [ -f "$active/exit.json" ] &&
    grep -q '"cleanupSucceeded":true' "$active/exit.json"; then
    printf '{"finishedUtc":"%s","action":"final_runner_already_restored"}\n' "$(utc)" > "$BASE/exit.json"
    exit 0
fi
printf '%s\n' "$(utc)" > "$BASE/deadline-fired.txt"
termination_ok=true
if [ -n "$active" ]; then
    printf 'session_recovery\n' > "$active/deadline-cancel.tmp"
    mv "$active/deadline-cancel.tmp" "$active/cancel"
    n=0
    while [ ! -f "$active/exit.json" ] && [ "$n" -lt 36 ]; do sleep 5; n=$((n+1)); done
    if [ -f "$active/runner-process-stat.txt" ]; then
        if ! stop_recorded_group "$active/runner-process-stat.txt"; then
            termination_ok=false
            printf '%s\n' 'Runner termination could not be verified' > "$BASE/termination-warning.txt"
        fi
    else
        termination_ok=false
        printf '%s\n' 'Missing runner identity; APK recovery withheld' > "$BASE/termination-warning.txt"
    fi
    # New final-stage runner persists these identities; old bridge has no extra AM after normal exit.
    stop_recorded_group "$active/active-am-process-stat.txt" || termination_ok=false
    stop_recorded_group "$active/network-process-stat.txt" || termination_ok=false
    if ! child_identity_evidence_ok "$active"; then
        termination_ok=false
        printf '%s\n' 'Incomplete child identity evidence; APK recovery withheld' > "$BASE/termination-warning.txt"
    fi
fi
ok=$termination_ok
timed am force-stop "$PACKAGE" || ok=false
# Restore screen behaviour before an APK installation could consume its watchdog allowance.
awake_stopped=false
if [ "$RESTORE_ORIGINAL_TIMEOUT" = true ]; then
    if stop_awake_guard; then awake_stopped=true; else ok=false; fi
else
    [ "$KEEP_AWAKE_ON_EXIT" != true ] || ORIGINAL_TIMEOUT=86400000
fi
timed settings put system screen_off_timeout "$ORIGINAL_TIMEOUT" || ok=false
timed settings put system screen_brightness "$ORIGINAL_BRIGHTNESS" || ok=false
timed settings put system screen_brightness_mode "$ORIGINAL_BRIGHTNESS_MODE" || ok=false
if [ "$termination_ok" = true ] && [ -n "${RESTORE_MAIN_APK:-}" ]; then
    expected=$(sha256sum "$RESTORE_MAIN_APK") || expected=
    if [ "${expected%% *}" = "$RESTORE_MAIN_SHA" ]; then
        main_path=$(timed pm path "$PACKAGE") || main_path=
        main_actual=$(sha256sum "${main_path#package:}" 2>/dev/null) || main_actual=
        if [ "${main_actual%% *}" != "$RESTORE_MAIN_SHA" ]; then
            /system/bin/toybox timeout -k 5 180 pm install -r -t "$RESTORE_MAIN_APK" > "$BASE/restore-main.log" 2>&1 || ok=false
        fi
    else ok=false; fi
fi
apk_path=$(timed pm path "$PACKAGE.test") || apk_path=
apk_path=${apk_path#package:}
actual=$(sha256sum "$apk_path" 2>/dev/null) || actual=
if [ "$termination_ok" = true ] && [ "${actual%% *}" != "$ORIGINAL_TEST_SHA" ]; then
    expected=$(sha256sum "$BASE/original-test.apk") || expected=
    if [ "${expected%% *}" = "$ORIGINAL_TEST_SHA" ]; then
        /system/bin/toybox timeout -k 5 120 pm install -r -t "$BASE/original-test.apk" > "$BASE/restore-test.log" 2>&1 || ok=false
    else ok=false; fi
fi
if [ "$ORIGINAL_BT" = 0 ]; then timed cmd bluetooth_manager disable || ok=false
else timed cmd bluetooth_manager enable || ok=false; fi
bt_ok=false
bt_tries=0
while [ "$bt_tries" -lt 12 ]; do
    /system/bin/toybox timeout -k 1 5 dumpsys bluetooth_manager > "$BASE/bluetooth.restored" || :
    if { [ "$ORIGINAL_BT" = 0 ] && grep -q 'state: OFF' "$BASE/bluetooth.restored"; } ||
       { [ "$ORIGINAL_BT" != 0 ] && grep -q 'state: ON' "$BASE/bluetooth.restored"; }; then
        bt_ok=true; break
    fi
    bt_tries=$((bt_tries+1)); sleep 1
done
[ "$bt_ok" = true ] || ok=false
timed settings put global bluetooth_on "$ORIGINAL_BT" || ok=false
apk_path=$(timed pm path "$PACKAGE.test") || apk_path=
apk_path=${apk_path#package:}
actual=$(sha256sum "$apk_path" 2>/dev/null) || actual=
[ "${actual%% *}" = "$ORIGINAL_TEST_SHA" ] || ok=false
main_path=$(timed pm path "$PACKAGE") || main_path=
main_path=${main_path#package:}
main_actual=$(sha256sum "$main_path" 2>/dev/null) || main_actual=
[ "${main_actual%% *}" = "${RESTORE_MAIN_SHA:-$MAIN_SHA}" ] || ok=false
if [ "${REQUIRE_BACKGROUND_JOURNALS:-false}" = true ]; then
    [ -f "$BASE/stopped-packages.txt" ] && [ -f "$BASE/enabled-packages.tsv" ] || ok=false
fi
if [ -f "$BASE/enabled-packages.tsv" ]; then
    while read -r enabled_package original_enabled; do
        case "$enabled_package" in ''|*[!A-Za-z0-9_.]*) ok=false; continue ;; esac
        case "$original_enabled" in
            0) enabled_command=default-state ;; 1) enabled_command=enable ;;
            2) enabled_command=disable ;; 3) enabled_command=disable-user ;;
            4) enabled_command=disable-until-used ;; *) ok=false; continue ;;
        esac
        timed su -c "cmd package $enabled_command --user 0 $enabled_package" >> "$BASE/background-enabled.log" 2>&1 || ok=false
        timed dumpsys package "$enabled_package" > "$BASE/enabled-$enabled_package.txt" || ok=false
        owner_package_state "$BASE/enabled-$enabled_package.txt" | grep -Eq " enabled=$original_enabled( |$)" || ok=false
    done < "$BASE/enabled-packages.tsv"
fi
if [ -f "$BASE/background-packages.txt" ]; then
    while IFS= read -r background_package; do
        case "$background_package" in ''|*[!A-Za-z0-9_.]*) ok=false; continue ;; esac
        timed su -c "cmd package unsuspend --user 0 $background_package" >> "$BASE/background-restored.log" 2>&1 || ok=false
        timed dumpsys package "$background_package" > "$BASE/background-$background_package.txt" || ok=false
        owner_package_state "$BASE/background-$background_package.txt" | grep -q ' suspended=false' || ok=false
    done < "$BASE/background-packages.txt"
fi
if [ -f "$BASE/stopped-packages.txt" ]; then
    while IFS= read -r stopped_package; do
        case "$stopped_package" in ''|*[!A-Za-z0-9_.]*) ok=false; continue ;; esac
        timed su -c "cmd package unstop --user 0 $stopped_package" >> "$BASE/background-unstopped.log" 2>&1 || ok=false
        timed dumpsys package "$stopped_package" > "$BASE/unstopped-$stopped_package.txt" || ok=false
        owner_package_state "$BASE/unstopped-$stopped_package.txt" | grep -q ' stopped=false' || ok=false
    done < "$BASE/stopped-packages.txt"
fi
if [ -n "${ORIGINAL_MUSIC_VOLUME:-}" ]; then
    timed cmd media_session volume --stream 3 --set "$ORIGINAL_MUSIC_VOLUME" > "$BASE/volume.restored" || ok=false
    timed cmd media_session volume --stream 3 --get >> "$BASE/volume.restored" || ok=false
    grep -q "volume is $ORIGINAL_MUSIC_VOLUME in range" "$BASE/volume.restored" || ok=false
    if [ -n "${ORIGINAL_MUSIC_MUTED:-}" ]; then
        timed dumpsys audio > "$BASE/audio.restored" || ok=false
        awk '/- STREAM_MUSIC:/ {music=1; next} music && /- STREAM_/ {exit} music {print}' "$BASE/audio.restored" > "$BASE/music.restored"
        grep -q "Muted: $ORIGINAL_MUSIC_MUTED" "$BASE/music.restored" || ok=false
    fi
fi
[ "$(timed settings get system screen_off_timeout)" = "$ORIGINAL_TIMEOUT" ] || ok=false
[ "$(timed settings get global bluetooth_on)" = "$ORIGINAL_BT" ] || ok=false
if [ "$ORIGINAL_BRIGHTNESS_MODE" != 1 ]; then
    [ "$(timed settings get system screen_brightness)" = "$ORIGINAL_BRIGHTNESS" ] || ok=false
fi
[ "$(timed settings get system screen_brightness_mode)" = "$ORIGINAL_BRIGHTNESS_MODE" ] || ok=false
# Once the real timeout and stopped awake guard have been restored, natural
# screen-off/keyguard is expected. Observe it without waking or forcing a lock.
final_screen_awake=false
timed dumpsys power > "$BASE/power.restored" || :
grep -Eq '^[[:space:]]*mWakefulness=Awake[[:space:]]*$' "$BASE/power.restored" && final_screen_awake=true
printf '{"finishedUtc":"%s","action":"session_recovery","trigger":"%s","cleanupSucceeded":%s,"keptAwake":%s,"awakeGuardStopped":%s,"originalTimeoutRestored":%s,"finalScreenAwake":%s}\n' "$(utc)" "$RECOVERY_TRIGGER" "$ok" "$KEEP_AWAKE_ON_EXIT" "$awake_stopped" "$RESTORE_ORIGINAL_TIMEOUT" "$final_screen_awake" > "$BASE/exit.json"
if [ "$ok" = true ] && [ "$(cat /data/local/tmp/ziv-power-exclusive.lock/owner.txt 2>/dev/null)" = "${SESSION_OWNER:-missing}" ]; then
    rm -f /data/local/tmp/ziv-power-exclusive.lock/owner.txt
    rmdir /data/local/tmp/ziv-power-exclusive.lock || :
fi
[ "$ok" = true ]
