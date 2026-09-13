#!/system/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# One detached shell session; every row starts a fresh instrumentation process.
set -u
BASE=$1
case "$BASE" in /data/local/tmp/ziv-power-20260913/*) ;; *) exit 64 ;; esac
. "$BASE/config.env"
PACKAGE=io.github.joyelliot.zivplayer
RUNNER=$PACKAGE.test/androidx.test.runner.AndroidJUnitRunner
AM_PID=
AM_START=
NETWORK_PID=
NETWORK_START_ID=
CURRENT_LABEL=
CURRENT_ORDINAL=0
STOP_REASON=initializing
LOW_TEST_INSTALLED=0
CLEANUP_OK=true
STARTED=0
FINISHING=0

timed() { /system/bin/toybox timeout -k 5 25 "$@"; }
uptime_s() { read uptime_value uptime_rest < /proc/uptime; printf '%s' "${uptime_value%%.*}"; }
utc() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
status() {
    if ! { printf '%s\t%s\t%s\t%s\t%s\n' "$(utc)" "$1" "$CURRENT_ORDINAL" "$CURRENT_LABEL" "$2" > "$BASE/status.tmp" &&
        mv "$BASE/status.tmp" "$BASE/status.tsv" && cat "$BASE/status.tsv" >> "$BASE/events.tsv"; }; then
        CLEANUP_OK=false
        STOP_REASON=status_evidence_write_failed
        [ "$FINISHING" -eq 1 ] || exit 70
        return 1
    fi
}
fail() { STOP_REASON=$1; status stopped "$STOP_REASON"; exit 1; }
cancelled() { [ -f "$BASE/cancel" ]; }
stop_app() {
    timed am force-stop "$PACKAGE" || return 1
    pidof "$PACKAGE" >/dev/null 2>&1
    result=$?
    [ "$result" -eq 1 ]
}
process_identity() {
    process_stat=$(cat "/proc/$1/stat" 2>/dev/null) || return 1
    process_fields=${process_stat##*) }
    set -- $process_fields
    PROCESS_GROUP=$3
    PROCESS_SESSION=$4
    shift 19
    PROCESS_START=$1
}
capture_child_identity() {
    capture_pid=$1
    n=0
    while [ "$n" -lt 10 ]; do
        if process_identity "$capture_pid" && [ "$PROCESS_GROUP" = "$capture_pid" ] && [ "$PROCESS_SESSION" = "$capture_pid" ]; then
            printf '%s' "$PROCESS_START"
            return 0
        fi
        sleep 0.1
        n=$((n+1))
    done
    return 1
}
stop_child_group() {
    child_pid=$1; child_start=$2
    [ -n "$child_pid" ] || return 0
    [ -n "$child_start" ] && [ "$child_pid" != "$$" ] || return 1
    if kill -0 "$child_pid" 2>/dev/null; then
        process_identity "$child_pid" || return 1
        [ "$PROCESS_START" = "$child_start" ] && [ "$PROCESS_GROUP" = "$child_pid" ] &&
            [ "$PROCESS_SESSION" = "$child_pid" ] || return 1
    elif ! kill -0 -- "-$child_pid" 2>/dev/null; then
        wait "$child_pid" 2>/dev/null || :; return 0
    fi
    kill -TERM -- "-$child_pid" 2>/dev/null || return 1
    n=0
    while kill -0 -- "-$child_pid" 2>/dev/null && [ "$n" -lt 50 ]; do sleep 0.1; n=$((n+1)); done
    if kill -0 -- "-$child_pid" 2>/dev/null; then
        if kill -0 "$child_pid" 2>/dev/null; then
            process_identity "$child_pid" || return 1
            [ "$PROCESS_START" = "$child_start" ] && [ "$PROCESS_GROUP" = "$child_pid" ] || return 1
        fi
        kill -KILL -- "-$child_pid" 2>/dev/null || return 1
        sleep 0.2
    fi
    kill -0 -- "-$child_pid" 2>/dev/null && return 1
    wait "$child_pid" 2>/dev/null || :
    return 0
}
read_battery() {
    timed dumpsys battery > "$BASE/battery.current" || return 1
    LEVEL=$(awk '$1=="level:" {print $2}' "$BASE/battery.current")
    TEMP=$(awk '$1=="temperature:" {print $2}' "$BASE/battery.current")
    VOLTAGE=$(awk '$1=="voltage:" {print $2}' "$BASE/battery.current")
    BATTERY_STATUS=$(awk '$1=="status:" {print $2}' "$BASE/battery.current")
    case "$LEVEL:$TEMP:$VOLTAGE:$BATTERY_STATUS" in *[!0-9:]*|:*|*::*|*:) return 1 ;; esac
    [ "$LEVEL" -le 100 ] && [ "$VOLTAGE" -ge 3000 ] && [ "$VOLTAGE" -le 5000 ] || return 1
    [ "$BATTERY_STATUS" -eq 3 ] || return 1
    ! grep -Eq '^  (AC|USB|Wireless|Dock) powered: true' "$BASE/battery.current"
}
activity_time() {
    awk -F= '/^[[:space:]]*(mLastUserActivityTime|lastUserActivityTime)=[0-9]+/ {
        value=$2; sub(/[^0-9].*/, "", value); seen[value]=1
    } END {for (value in seen) {count++; result=value} if (count==1) print result; else exit 1}' "$BASE/power.current"
}
screen_state() {
    timed dumpsys power > "$BASE/power.current" || return 1
    timed dumpsys window policy > "$BASE/policy.current" || return 1
    grep -Eq '^[[:space:]]*mWakefulness=Awake[[:space:]]*$' "$BASE/power.current" || return 1
    grep -Eq '^[[:space:]]*showing=false[[:space:]]*$' "$BASE/policy.current" || return 1
    ACTIVITY=$(activity_time) || return 1
    [ -n "$ACTIVITY" ]
}
refresh_home() {
    screen_state || return 1
    prior_activity=$ACTIVITY
    timed input keyevent 3 || return 1
    attempt=0
    while [ "$attempt" -lt 6 ]; do
        screen_state || return 1
        [ "$ACTIVITY" -gt "$prior_activity" ] && return 0
        sleep 0.5
        attempt=$((attempt+1))
    done
    return 1
}
bluetooth_off() {
    timed cmd bluetooth_manager disable >/dev/null || return 1
    attempt=0
    while [ "$attempt" -lt 15 ]; do
        value=$(timed settings get global bluetooth_on) || return 1
        if [ "$value" = 0 ]; then
            timed dumpsys bluetooth_manager > "$BASE/bluetooth.off" || return 1
            grep -q 'enabled: false' "$BASE/bluetooth.off" && grep -q 'state: OFF' "$BASE/bluetooth.off" && return 0
        fi
        sleep 1
        attempt=$((attempt+1))
    done
    return 1
}
restore_bluetooth() {
    if [ "$ORIGINAL_BT" = 0 ]; then
        bluetooth_off || return 1
    else
        timed cmd bluetooth_manager enable >/dev/null || return 1
        attempt=0
        while [ "$attempt" -lt 15 ]; do
            timed dumpsys bluetooth_manager > "$BASE/bluetooth.restored" || return 1
            if grep -q 'enabled: true' "$BASE/bluetooth.restored" && grep -q 'state: ON' "$BASE/bluetooth.restored"; then
                timed settings put global bluetooth_on "$ORIGINAL_BT" || return 1
                [ "$(timed settings get global bluetooth_on)" = "$ORIGINAL_BT" ] && return 0
            fi
            sleep 1
            attempt=$((attempt+1))
        done
        return 1
    fi
}
package_hash() {
    path=$(timed pm path "$1") || return 1
    case "$path" in package:/data/app/*/base.apk) path=${path#package:} ;; *) return 1 ;; esac
    hash=$(sha256sum "$path") || return 1
    printf '%s' "${hash%% *}"
}
install_test() {
    apk=$1
    expected=$2
    actual=$(sha256sum "$apk") || return 1
    [ "${actual%% *}" = "$expected" ] || return 1
    /system/bin/toybox timeout -k 5 120 pm install -r -t "$apk" > "$BASE/test-install-last.log" 2>&1 || return 1
    [ "$(package_hash "$PACKAGE.test")" = "$expected" ] || return 1
    [ "$(package_hash "$PACKAGE")" = "$MAIN_SHA" ]
}
collection_window() {
    [ -f "$BASE/collection.request" ] || return 0
    window_start=$(uptime_s)
    window_end=$((window_start+60))
    printf '{"openedUptimeSeconds":%s,"closesAfterUptimeSeconds":%s,"completedThroughOrdinal":%s}\n' \
        "$window_start" "$window_end" "$((CURRENT_ORDINAL-1))" > "$BASE/collection.grant.tmp" || fail collection_grant_failed
    mv "$BASE/collection.grant.tmp" "$BASE/collection.grant.json" || fail collection_grant_failed
    status collection_window "$window_end"
    refresh_home || fail screen_lost_during_collection
    while [ "$(uptime_s)" -lt "$window_end" ] && [ ! -f "$BASE/collection.done" ]; do
        cancelled && { STOP_REASON=user_cancelled; exit 0; }
        sleep 1
    done
    rm -f -- "$BASE/collection.request" "$BASE/collection.done" "$BASE/collection.grant.json" || fail collection_close_failed
    status cooling collection_window_closed
}
finish() {
    exit_code=$?
    trap - EXIT TERM INT
    FINISHING=1
    [ "$STARTED" -eq 1 ] || exit "$exit_code"
    status restoring "$STOP_REASON"
    if [ -n "$AM_PID" ] && kill -0 "$AM_PID" 2>/dev/null; then
        timed am force-stop "$PACKAGE" >/dev/null 2>&1 || CLEANUP_OK=false
        stop_child_group "$AM_PID" "$AM_START" || CLEANUP_OK=false
    fi
    stop_app || CLEANUP_OK=false
    stop_child_group "$NETWORK_PID" "$NETWORK_START_ID" || CLEANUP_OK=false
    if [ "$LOW_TEST_INSTALLED" -eq 1 ]; then
        install_test "$LEGACY_TEST_APK" "$LEGACY_TEST_SHA" || CLEANUP_OK=false
    fi
    timed settings put system screen_off_timeout "$ORIGINAL_TIMEOUT" || CLEANUP_OK=false
    [ "$(timed settings get system screen_off_timeout)" = "$ORIGINAL_TIMEOUT" ] || CLEANUP_OK=false
    restore_bluetooth || CLEANUP_OK=false
    timed settings put system screen_brightness "$ORIGINAL_BRIGHTNESS" || CLEANUP_OK=false
    timed settings put system screen_brightness_mode "$ORIGINAL_BRIGHTNESS_MODE" || CLEANUP_OK=false
    [ "$(timed settings get system screen_brightness)" = "$ORIGINAL_BRIGHTNESS" ] || CLEANUP_OK=false
    [ "$(timed settings get system screen_brightness_mode)" = "$ORIGINAL_BRIGHTNESS_MODE" ] || CLEANUP_OK=false
    sleep_verified=false
    DO_SLEEP=$SLEEP_AT_END
    [ "$STOP_REASON" = battery_below_10_after_leg ] && DO_SLEEP=true
    if [ "$DO_SLEEP" = true ]; then
        timed input keyevent 223 || CLEANUP_OK=false
        sleep 2
        timed dumpsys power > "$BASE/power.restored" || CLEANUP_OK=false
        if grep -Eq '^[[:space:]]*mWakefulness=(Dozing|Asleep)[[:space:]]*$' "$BASE/power.restored"; then
            sleep_verified=true
        else
            CLEANUP_OK=false
        fi
    fi
    read_battery || CLEANUP_OK=false
    status finished "$STOP_REASON"
    rmdir "$BASE/active.lock" 2>/dev/null || CLEANUP_OK=false
    printf '{"finishedUtc":"%s","stopReason":"%s","runnerExitCode":%s,"cleanupSucceeded":%s,"screenTimeout":"%s","bluetooth":"%s","sleepRequested":%s,"sleepVerified":%s,"batteryPercent":%s}\n' \
        "$(utc)" "$STOP_REASON" "$exit_code" "$CLEANUP_OK" "$ORIGINAL_TIMEOUT" "$ORIGINAL_BT" "$DO_SLEEP" "$sleep_verified" "${LEVEL:-0}" > "$BASE/exit.tmp" || exit 70
    mv "$BASE/exit.tmp" "$BASE/exit.json" || exit 70
    [ "$CLEANUP_OK" = true ] || exit 2
    exit "$exit_code"
}

mkdir "$BASE/active.lock" || exit 73
trap 'rmdir "$BASE/active.lock" 2>/dev/null || :' EXIT
[ ! -e "$BASE/started.json" ] || { rmdir "$BASE/active.lock"; exit 73; }
actual_manifest=$(sha256sum "$BASE/manifest.tsv") || exit 65
[ "${actual_manifest%% *}" = "$MANIFEST_SHA" ] || exit 65
awk -F '\t' -v expected="$MANIFEST_ROWS" '
    NF!=9 || $1!=NR || $2!~/^[12]$/ || $4!~/^[1-4]$/ || $3!~/^[A-Za-z0-9:_-]+$/ ||
    $5!~/^(avc|hevc|hevc10|vp9|av1)$/ || $6!~/^(automatic|software)$/ ||
    $7!~/^(exo|libmpv)$/ || $8!~/^[A-Za-z0-9_-]+$/ || seen[$8]++ ||
    $9!~/^(cross-day-bridge|matrix|validation-only)$/ {bad=1}
    END {exit (bad || NR!=expected || NR==0)}' "$BASE/manifest.tsv" || exit 65
ORIGINAL_TIMEOUT=$(timed settings get system screen_off_timeout) || exit 65
ORIGINAL_BT=$(timed settings get global bluetooth_on) || exit 65
ORIGINAL_BRIGHTNESS=$(timed settings get system screen_brightness) || exit 65
ORIGINAL_BRIGHTNESS_MODE=$(timed settings get system screen_brightness_mode) || exit 65
[ "$ORIGINAL_BRIGHTNESS" = 1 ] && [ "$ORIGINAL_BRIGHTNESS_MODE" = 0 ] || exit 65
case "$ORIGINAL_TIMEOUT" in ''|*[!0-9]*) exit 65 ;; esac
case "$ORIGINAL_BT" in 0|1|2) ;; *) exit 65 ;; esac
STARTED=1
trap finish EXIT
trap 'STOP_REASON=signal_cancelled; exit 130' TERM INT
printf '{"startedUtc":"%s","pid":%s,"screenTimeout":"%s","bluetooth":"%s"}\n' "$(utc)" "$$" "$ORIGINAL_TIMEOUT" "$ORIGINAL_BT" > "$BASE/started.json" || fail startup_evidence_failed
echo "$$" > "$BASE/runner.pid" || fail startup_evidence_failed
cat "/proc/$$/stat" > "$BASE/runner-process-stat.txt" || fail startup_evidence_failed
mkdir -p "$BASE/raw" "$BASE/control" || fail evidence_directory_failed
[ "$(package_hash "$PACKAGE")" = "$MAIN_SHA" ] || fail main_apk_changed
ACTIVE_TEST_SHA=$(package_hash "$PACKAGE.test") || fail test_apk_unreadable
[ "$ACTIVE_TEST_SHA" = "$EXPECTED_INITIAL_TEST_SHA" ] || fail test_apk_changed
stop_app || fail app_process_not_stopped
refresh_home || fail screen_not_unlocked_awake
timed settings put system screen_off_timeout 900000 || fail screen_timeout_setup_failed
bluetooth_off || fail bluetooth_setup_failed
timed settings put system screen_brightness_mode 0 || fail brightness_mode_failed
timed cmd display set-brightness 0 || fail brightness_setup_failed
/system/bin/setsid ip -ts monitor link dev wlan0 > "$BASE/network-link-events.txt" 2> "$BASE/network-link-error.txt" &
NETWORK_PID=$!
NETWORK_START_ID=$(capture_child_identity "$NETWORK_PID") || fail network_monitor_identity_unavailable
kill -0 "$NETWORK_PID" 2>/dev/null || fail network_monitor_unavailable
[ ! -s "$BASE/network-link-error.txt" ] || fail network_monitor_error

exec 3< "$BASE/manifest.tsv" || fail manifest_open_failed
processed_rows=0
while IFS="$(printf '\t')" read -r ORDINAL BLOCK_NUMBER BLOCK_ID POSITION CODEC DECODER ENGINE LABEL PURPOSE <&3; do
    [ -n "$ORDINAL" ] || continue
    CURRENT_ORDINAL=$ORDINAL
    CURRENT_LABEL=$LABEL
    case "$LABEL" in ''|*[!A-Za-z0-9_-]*) fail invalid_manifest_label ;; esac
    cancelled && { STOP_REASON=user_cancelled; exit 0; }
    stop_app || fail app_process_not_stopped
    mkdir "$BASE/control/$LABEL.claim" || fail result_label_already_claimed
    printf 'STOPPED\n' > "$BASE/control/$LABEL.process-before.txt" || fail process_evidence_failed
    for suffix in json jsonl; do
        presence=$(timed run-as "$PACKAGE" sh -c 'if test -e "$1"; then printf PRESENT; else printf ABSENT; fi' sh "files/power-results/$LABEL.$suffix") || fail result_presence_probe_failed
        [ "$presence" = ABSENT ] || fail result_already_exists
    done
    read_battery || fail supply_or_battery_probe_failed
    [ "$LEVEL" -ge 10 ] || { STOP_REASON=battery_below_10_after_leg; exit 0; }
    # The legacy APK stays in use for comparable high-battery runs. The updated
    # guard is installed only when the old 25% guard could truncate a later leg.
    if [ "$LEVEL" -lt 28 ] && [ "$ACTIVE_TEST_SHA" = "$LEGACY_TEST_SHA" ]; then
        status installing_low_battery_test "$LEVEL"
        LOW_TEST_INSTALLED=1
        install_test "$LOW_TEST_APK" "$LOW_TEST_SHA" || fail low_battery_test_install_failed
        ACTIVE_TEST_SHA=$LOW_TEST_SHA
    fi
    while :; do
    cooldown_started=$(uptime_s)
    status cooling "$LEVEL"
    while :; do
        cancelled && { STOP_REASON=user_cancelled; exit 0; }
        collection_window
        refresh_home || fail screen_lost_during_cooldown
        read_battery || fail supply_or_battery_probe_failed
        [ "$LEVEL" -ge 10 ] || { STOP_REASON=battery_below_10_after_leg; exit 0; }
        if [ "$LEVEL" -lt 28 ] && [ "$ACTIVE_TEST_SHA" = "$LEGACY_TEST_SHA" ]; then
            status installing_low_battery_test "$LEVEL"
            LOW_TEST_INSTALLED=1
            install_test "$LOW_TEST_APK" "$LOW_TEST_SHA" || fail low_battery_test_install_failed
            ACTIVE_TEST_SHA=$LOW_TEST_SHA
            cooldown_started=$(uptime_s)
            continue
        fi
        cooldown_elapsed=$(( $(uptime_s) - cooldown_started ))
        if [ "$cooldown_elapsed" -ge "$((MINIMUM_COOLDOWN+1))" ] && [ "$TEMP" -le 360 ]; then break; fi
        [ "$cooldown_elapsed" -lt 1200 ] || fail cooling_deadline_expired
        # A bounded wait also observes cancellation without depending on ADB.
        n=0
        while [ "$n" -lt 6 ]; do
            cancelled && { STOP_REASON=user_cancelled; exit 0; }
            sleep 5
            n=$((n+1))
        done
    done
    refresh_home || fail screen_lost_before_playback
    timed cmd display set-brightness 0 || fail brightness_setup_failed
    read_battery || fail supply_or_battery_probe_failed
    [ "$TEMP" -le 360 ] || fail temperature_changed_before_playback
    [ "$LEVEL" -ge 10 ] || { STOP_REASON=battery_below_10_after_leg; exit 0; }
    if [ "$LEVEL" -lt 28 ] && [ "$ACTIVE_TEST_SHA" = "$LEGACY_TEST_SHA" ]; then
        LOW_TEST_INSTALLED=1
        install_test "$LOW_TEST_APK" "$LOW_TEST_SHA" || fail low_battery_test_install_failed
        ACTIVE_TEST_SHA=$LOW_TEST_SHA
        continue
    fi
    break
    done
    PRE_LEVEL=$LEVEL; PRE_TEMP=$TEMP; PRE_VOLTAGE=$VOLTAGE; PRE_ACTIVITY=$ACTIVITY
    cp "$BASE/battery.current" "$BASE/control/$LABEL.battery-before.txt" || fail preflight_evidence_failed
    cp "$BASE/power.current" "$BASE/control/$LABEL.power-before.txt" || fail preflight_evidence_failed
    cp "$BASE/policy.current" "$BASE/control/$LABEL.policy-before.txt" || fail preflight_evidence_failed
    timed cmd wifi status > "$BASE/control/$LABEL.wifi-before.txt" || fail wifi_probe_failed
    NETWORK_START=$(wc -c < "$BASE/network-link-events.txt")
    START_UTC=$(utc)
    START_UPTIME=$(uptime_s)
    status running "$LEVEL"
    /system/bin/setsid /system/bin/toybox timeout --foreground -k 10 480 am instrument -w -r \
        -e powerBenchmark true -e engine "$ENGINE" -e fixture "$CODEC" \
        -e decoderMode "$DECODER" -e surfaceProtocol matched-1080p \
        -e measurementCohort "$COHORT" -e sleepOnFailure false \
        -e validationOnly "$VALIDATION_ONLY" -e allowExternalPower false \
        -e minimumBatteryPercent 5 -e runLabel "$LABEL" \
        -e warmupSeconds "$WARMUP_SECONDS" -e measureSeconds "$MEASURE_SECONDS" \
        -e class io.github.joyelliot.zivplayer.PowerPlaybackBenchmarkTest \
        "$RUNNER" </dev/null > "$BASE/raw/$LABEL.instrumentation.log" 2>&1 &
    AM_PID=$!
    AM_START=$(capture_child_identity "$AM_PID") || fail instrumentation_process_identity_failed
    cat "/proc/$AM_PID/stat" > "$BASE/control/$LABEL.am-process-stat.txt" || fail process_evidence_failed
    APP_PID_SEEN=
    command_timed_out=false
    while kill -0 "$AM_PID" 2>/dev/null; do
        cancelled && { STOP_REASON=user_cancelled; exit 0; }
        if [ -z "$APP_PID_SEEN" ]; then
            APP_PID_SEEN=$(pidof "$PACKAGE") || :
            if [ -n "$APP_PID_SEEN" ]; then printf '%s\n' "$APP_PID_SEEN" > "$BASE/control/$LABEL.app-process.txt" || fail process_evidence_failed; fi
        fi
        if [ "$(( $(uptime_s) - START_UPTIME ))" -gt 600 ]; then
            command_timed_out=true
            timed am force-stop "$PACKAGE" || fail timed_out_app_stop_failed
            stop_child_group "$AM_PID" "$AM_START" || fail timed_out_child_stop_failed
            break
        fi
        sleep 5
    done
    wait "$AM_PID"
    COMMAND_EXIT=$?
    case "$COMMAND_EXIT" in 124|137|143) command_timed_out=true ;; esac
    stop_child_group "$AM_PID" "$AM_START" || fail instrumentation_child_remained
    AM_PID=
    END_UTC=$(utc)
    END_UPTIME=$(uptime_s)
    NETWORK_END=$(wc -c < "$BASE/network-link-events.txt")
    NETWORK_ALIVE=false
    kill -0 "$NETWORK_PID" 2>/dev/null && [ ! -s "$BASE/network-link-error.txt" ] && NETWORK_ALIVE=true
    complete_result=false
    grace_started=$(uptime_s)
    while [ "$(( $(uptime_s) - grace_started ))" -lt 120 ]; do
        if timed run-as "$PACKAGE" cat "files/power-results/$LABEL.json" > "$BASE/raw/$LABEL.json.tmp" 2>/dev/null &&
           grep -Eq '"cleanupSucceeded": (true|false)' "$BASE/raw/$LABEL.json.tmp" &&
           [ "$(tail -c 1 "$BASE/raw/$LABEL.json.tmp")" = '}' ]; then
            mv "$BASE/raw/$LABEL.json.tmp" "$BASE/raw/$LABEL.json" || fail finalized_result_move_failed
            complete_result=true
            break
        fi
        cancelled && { STOP_REASON=user_cancelled; exit 0; }
        sleep 2
    done
    timed run-as "$PACKAGE" cat "files/power-results/$LABEL.jsonl" > "$BASE/raw/$LABEL.jsonl" 2> "$BASE/raw/$LABEL.trace-error.txt" || :
    timed cmd wifi status > "$BASE/control/$LABEL.wifi-after.txt" || :
    read_battery || fail supply_or_battery_probe_failed
    cp "$BASE/battery.current" "$BASE/control/$LABEL.battery-after.txt" || fail postflight_evidence_failed
    FRESH_PROCESS=false
    case "$APP_PID_SEEN" in ''|*[!0-9]*) APP_PID_SEEN=0 ;; *) FRESH_PROCESS=true ;; esac
    printf '{"ordinal":%s,"blockNumber":%s,"comparisonBlock":"%s","plannedOrdinal":%s,"codec":"%s","decoderMode":"%s","engine":"%s","runLabel":"%s","purpose":"%s","startedUtc":"%s","finishedUtc":"%s","startUptimeSeconds":%s,"endUptimeSeconds":%s,"exitCode":%s,"commandTimedOut":%s,"finalResultSeen":%s,"preflightBattery":{"plugged":false,"status":3,"level":%s,"temperatureTenthsC":%s,"voltageMv":%s},"afterBatteryPercent":%s,"cooldownSeconds":%s,"freshProcessVerified":%s,"newAppPid":%s,"screenActivityBeforeLeg":{"wakefulness":"Awake","unlocked":true,"lastUserActivity":%s},"temporaryScreenTimeoutMs":900000,"testApkSha256":"%s","networkStartBytes":%s,"networkEndBytes":%s,"networkMonitorAlive":%s}\n' \
        "$ORDINAL" "$BLOCK_NUMBER" "$BLOCK_ID" "$POSITION" "$CODEC" "$DECODER" "$ENGINE" "$LABEL" "$PURPOSE" \
        "$START_UTC" "$END_UTC" "$START_UPTIME" "$END_UPTIME" "$COMMAND_EXIT" "$command_timed_out" "$complete_result" \
        "$PRE_LEVEL" "$PRE_TEMP" "$PRE_VOLTAGE" "$LEVEL" "$cooldown_elapsed" "$FRESH_PROCESS" "$APP_PID_SEEN" "$PRE_ACTIVITY" "$ACTIVE_TEST_SHA" \
        "$NETWORK_START" "$NETWORK_END" "$NETWORK_ALIVE" > "$BASE/control/$LABEL.control.json.tmp" || fail control_evidence_write_failed
    mv "$BASE/control/$LABEL.control.json.tmp" "$BASE/control/$LABEL.control.json" || fail control_evidence_move_failed
    status leg_finished "$LEVEL"
    stop_app || fail app_process_not_stopped
    [ "$LEVEL" -ge 10 ] || { STOP_REASON=battery_below_10_after_leg; exit 0; }
    processed_rows=$((processed_rows+1))
    # Recorded playback, quality, or comparison failures do not stop the matrix.
done
exec 3<&-
[ "$processed_rows" -eq "$MANIFEST_ROWS" ] || fail manifest_not_fully_processed
STOP_REASON=manifest_completed
exit 0
