#!/system/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# One detached shell session; every row starts a fresh instrumentation process.
set -u
BASE=$1
case "$BASE" in /data/local/tmp/ziv-power-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]/*) ;; *) exit 64 ;; esac
. "$BASE/config.env"
SURFACE_PROTOCOL=${SURFACE_PROTOCOL:-matched-1080p}
MPV_VIDEO_OUTPUT=${MPV_VIDEO_OUTPUT:-gpu-next}
case "$MPV_VIDEO_OUTPUT" in gpu-next|gpu) ;; *) exit 2 ;; esac
FRAME_PROOF_METHOD=${FRAME_PROOF_METHOD:-surface-pixelcopy-v1}
SCREEN_TIMEOUT_MS=${SCREEN_TIMEOUT_MS:-900000}
HARDWARE_ONLY_AUTOMATIC=${HARDWARE_ONLY_AUTOMATIC:-false}
HARD_STOP_EPOCH=${HARD_STOP_EPOCH:-0}
OPTIONAL_BLOCK_BUDGET_SECONDS=${OPTIONAL_BLOCK_BUDGET_SECONDS:-2400}
HARD_STOP_UPTIME=0
KEEP_AWAKE_ON_EXIT=${KEEP_AWAKE_ON_EXIT:-false}
COOLDOWN_POLL_SECONDS=${COOLDOWN_POLL_SECONDS:-30}
MAX_START_TEMPERATURE_TENTHS=${MAX_START_TEMPERATURE_TENTHS:-360}
COOLDOWN_REQUIRE_NONRISING=${COOLDOWN_REQUIRE_NONRISING:-false}
COOLDOWN_INPUT_REFRESH=${COOLDOWN_INPUT_REFRESH:-true}
FIRST_COOLDOWN_SECONDS=${FIRST_COOLDOWN_SECONDS:-$MINIMUM_COOLDOWN}
MINIMUM_START_BATTERY=${MINIMUM_START_BATTERY:-10}
MINIMUM_SAMPLE_BATTERY=${MINIMUM_SAMPLE_BATTERY:-5}
SOFTWARE_ONLY=${SOFTWARE_ONLY:-false}
SKIPPED_BUDGET_BLOCK=
SKIPPED_BLOCK_REASON=
AV1_DECISION=undecided
EXTRA_DECISION=undecided
TARGET_SOFTWARE_FINISHED=0
TARGET_SOFTWARE_OK=true
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
cancelled() {
    if [ -f "$BASE/cancel" ]; then
        STOP_REASON=user_cancelled
        read cancel_reason < "$BASE/cancel" || cancel_reason=
        [ "$cancel_reason" != night_deadline ] || STOP_REASON=night_deadline
        return 0
    fi
    if [ "$HARD_STOP_UPTIME" -gt 0 ]; then
        read deadline_uptime deadline_unused < /proc/uptime || { STOP_REASON=deadline_clock_unavailable; return 0; }
        if [ "${deadline_uptime%%.*}" -ge "$HARD_STOP_UPTIME" ]; then
            STOP_REASON=night_deadline
            return 0
        fi
    fi
    return 1
}
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
        cancelled && exit 0
        sleep 1
    done
    rm -f -- "$BASE/collection.request" "$BASE/collection.done" "$BASE/collection.grant.json" || fail collection_close_failed
    status cooling collection_window_closed
}
restore_session_settings() {
    if [ -n "${RESTORE_TEST_APK:-}" ]; then
        install_test "$RESTORE_TEST_APK" "$RESTORE_TEST_SHA" || CLEANUP_OK=false
    fi
    recovery_screen_timeout=$ORIGINAL_TIMEOUT
    [ "$KEEP_AWAKE_ON_EXIT" != true ] || recovery_screen_timeout=$SCREEN_TIMEOUT_MS
    timed settings put system screen_off_timeout "$recovery_screen_timeout" || CLEANUP_OK=false
    [ "$(timed settings get system screen_off_timeout)" = "$recovery_screen_timeout" ] || CLEANUP_OK=false
    restore_bluetooth || CLEANUP_OK=false
    timed settings put system screen_brightness "$ORIGINAL_BRIGHTNESS" || CLEANUP_OK=false
    timed settings put system screen_brightness_mode "$ORIGINAL_BRIGHTNESS_MODE" || CLEANUP_OK=false
    [ "$(timed settings get system screen_brightness)" = "$ORIGINAL_BRIGHTNESS" ] || CLEANUP_OK=false
    [ "$(timed settings get system screen_brightness_mode)" = "$ORIGINAL_BRIGHTNESS_MODE" ] || CLEANUP_OK=false
}
finish() {
    exit_code=$?
    trap - EXIT TERM INT
    FINISHING=1
    [ "$STARTED" -eq 1 ] || exit "$exit_code"
    if [ -n "$CURRENT_LABEL" ] && [ ! -f "$BASE/control/$CURRENT_LABEL.control.json" ] && [ -n "$AM_PID" ]; then
        status leg_interrupted "$STOP_REASON"
    fi
    status restoring "$STOP_REASON"
    if [ -n "$AM_PID" ] && kill -0 "$AM_PID" 2>/dev/null; then
        timed am force-stop "$PACKAGE" >/dev/null 2>&1 || CLEANUP_OK=false
        stop_child_group "$AM_PID" "$AM_START" || CLEANUP_OK=false
    fi
    stop_app || CLEANUP_OK=false
    stop_child_group "$NETWORK_PID" "$NETWORK_START_ID" || CLEANUP_OK=false
    recovery_apk=${RESTORE_TEST_APK:-}
    recovery_timeout=$ORIGINAL_TIMEOUT
    recovery_bt=$ORIGINAL_BT
    holding=false
    # A normal intermediate phase may hold the session open. Every failure or
    # battery stop retains the full original recovery configuration instead.
    if [ "${HOLD_FOR_NEXT_STAGE:-false}" = true ] && [ "$STOP_REASON" = manifest_completed ] &&
       [ "$exit_code" -eq 0 ] && [ "$CLEANUP_OK" = true ]; then
        read_battery || CLEANUP_OK=false
        if [ "$CLEANUP_OK" = true ] && [ "$LEVEL" -ge 10 ]; then
            RESTORE_TEST_APK=
            ORIGINAL_TIMEOUT=$SCREEN_TIMEOUT_MS
            ORIGINAL_BT=0
            SLEEP_AT_END=false
            holding=true
        fi
    fi
    if [ "$LOW_TEST_INSTALLED" -eq 1 ]; then
        install_test "$LEGACY_TEST_APK" "$LEGACY_TEST_SHA" || CLEANUP_OK=false
    fi
    restore_session_settings
    read_battery || CLEANUP_OK=false
    status finished "$STOP_REASON"
    rmdir "$BASE/active.lock" 2>/dev/null || CLEANUP_OK=false
    if [ "$holding" = true ] && { [ "$CLEANUP_OK" != true ] || [ "$LEVEL" -lt 10 ]; }; then
        # Keep the original recovery values until the hold has actually succeeded.
        RESTORE_TEST_APK=$recovery_apk
        ORIGINAL_TIMEOUT=$recovery_timeout
        ORIGINAL_BT=$recovery_bt
        SLEEP_AT_END=true
        [ "$LEVEL" -ge 10 ] || STOP_REASON=battery_below_10_after_leg
        restore_session_settings
    fi
    sleep_verified=false
    DO_SLEEP=$SLEEP_AT_END
    [ "$STOP_REASON" = battery_below_10_after_leg ] && DO_SLEEP=true
    [ "$KEEP_AWAKE_ON_EXIT" != true ] || DO_SLEEP=false
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
    printf '{"finishedUtc":"%s","stopReason":"%s","runnerExitCode":%s,"cleanupSucceeded":%s,"screenTimeout":"%s","bluetooth":"%s","sleepRequested":%s,"sleepVerified":%s,"batteryPercent":%s}\n' \
        "$(utc)" "$STOP_REASON" "$exit_code" "$CLEANUP_OK" "${recovery_screen_timeout:-$ORIGINAL_TIMEOUT}" "$ORIGINAL_BT" "$DO_SLEEP" "$sleep_verified" "${LEVEL:-0}" > "$BASE/exit.tmp" || exit 70
    mv "$BASE/exit.tmp" "$BASE/exit.json" || exit 70
    [ "$CLEANUP_OK" = true ] || exit 2
    exit "$exit_code"
}

case "$HARD_STOP_EPOCH:$OPTIONAL_BLOCK_BUDGET_SECONDS" in *[!0-9:]*|:*|*:) exit 64 ;; esac
if [ "$HARD_STOP_EPOCH" -gt 0 ]; then
    deadline_remaining=$((HARD_STOP_EPOCH-$(date +%s)))
    [ "$deadline_remaining" -gt 0 ] || exit 64
    HARD_STOP_UPTIME=$(( $(uptime_s)+deadline_remaining ))
fi
mkdir "$BASE/active.lock" || exit 73
trap 'rmdir "$BASE/active.lock" 2>/dev/null || :' EXIT
[ ! -e "$BASE/started.json" ] || { rmdir "$BASE/active.lock"; exit 73; }
actual_manifest=$(sha256sum "$BASE/manifest.tsv") || exit 65
[ "${actual_manifest%% *}" = "$MANIFEST_SHA" ] || exit 65
awk -F '\t' -v expected="$MANIFEST_ROWS" -v software_only="$SOFTWARE_ONLY" '
    (NF!=9 && NF!=10) || (NF==10 && $10!~/^(gpu-next|gpu)$/) ||
    (software_only=="true" && $6!="software") ||
    $1!=NR || $2!~/^[12]$/ || $4!~/^[1-4]$/ || $3!~/^[A-Za-z0-9:_-]+$/ ||
    $5!~/^(avc|hevc|hevc10|vp9|av1)$/ || $6!~/^(automatic|software)$/ ||
    $7!~/^(exo|libmpv|vlc)$/ || $8!~/^[A-Za-z0-9_-]+$/ || seen[$8]++ ||
    $9!~/^(cross-day-bridge|matrix|validation-only|exo-screen|exo-conditional|av1-conditional|optional-software|software-preflight|battery-extra)$/ {bad=1}
    END {exit (bad || NR!=expected || NR==0)}' "$BASE/manifest.tsv" || exit 65
ORIGINAL_TIMEOUT=$(timed settings get system screen_off_timeout) || exit 65
ORIGINAL_BT=$(timed settings get global bluetooth_on) || exit 65
ORIGINAL_BRIGHTNESS=$(timed settings get system screen_brightness) || exit 65
ORIGINAL_BRIGHTNESS_MODE=$(timed settings get system screen_brightness_mode) || exit 65
ORIGINAL_TIMEOUT=${RESTORE_SCREEN_TIMEOUT:-$ORIGINAL_TIMEOUT}
ORIGINAL_BT=${RESTORE_BLUETOOTH:-$ORIGINAL_BT}
[ "$ORIGINAL_BRIGHTNESS" = 1 ] && [ "$ORIGINAL_BRIGHTNESS_MODE" = 0 ] || exit 65
case "$ORIGINAL_TIMEOUT" in ''|*[!0-9]*) exit 65 ;; esac
case "$ORIGINAL_BT" in 0|1|2) ;; *) exit 65 ;; esac
STARTED=1
trap finish EXIT
trap 'STOP_REASON=signal_cancelled; exit 130' TERM INT
printf '{"startedUtc":"%s","pid":%s,"screenTimeout":"%s","bluetooth":"%s"}\n' "$(utc)" "$$" "$ORIGINAL_TIMEOUT" "$ORIGINAL_BT" > "$BASE/started.tmp" || fail startup_evidence_failed
echo "$$" > "$BASE/runner.pid" || fail startup_evidence_failed
cat "/proc/$$/stat" > "$BASE/runner-process-stat.txt" || fail startup_evidence_failed
mv "$BASE/started.tmp" "$BASE/started.json" || fail startup_evidence_failed
mkdir -p "$BASE/raw" "$BASE/control" "$BASE/decisions" || fail evidence_directory_failed
[ "$(package_hash "$PACKAGE")" = "$MAIN_SHA" ] || fail main_apk_changed
ACTIVE_TEST_SHA=$(package_hash "$PACKAGE.test") || fail test_apk_unreadable
[ "$ACTIVE_TEST_SHA" = "$EXPECTED_INITIAL_TEST_SHA" ] || fail test_apk_changed
stop_app || fail app_process_not_stopped
refresh_home || fail screen_not_unlocked_awake
timed settings put system screen_off_timeout "$SCREEN_TIMEOUT_MS" || fail screen_timeout_setup_failed
[ "$(timed settings get system screen_off_timeout)" = "$SCREEN_TIMEOUT_MS" ] || fail screen_timeout_not_applied
bluetooth_off || fail bluetooth_setup_failed
timed settings put system screen_brightness_mode 0 || fail brightness_mode_failed
timed cmd display set-brightness 0 || fail brightness_setup_failed
/system/bin/setsid ip -ts monitor link dev wlan0 > "$BASE/network-link-events.txt" 2> "$BASE/network-link-error.txt" &
NETWORK_PID=$!
NETWORK_START_ID=$(capture_child_identity "$NETWORK_PID") || fail network_monitor_identity_unavailable
printf '%s\n' "$NETWORK_PID" > "$BASE/network.pid" || fail network_monitor_identity_unavailable
cat "/proc/$NETWORK_PID/stat" > "$BASE/network-process-stat.txt" || fail network_monitor_identity_unavailable
kill -0 "$NETWORK_PID" 2>/dev/null || fail network_monitor_unavailable
[ ! -s "$BASE/network-link-error.txt" ] || fail network_monitor_error

exec 3< "$BASE/manifest.tsv" || fail manifest_open_failed
processed_rows=0
while IFS="$(printf '\t')" read -r ORDINAL BLOCK_NUMBER BLOCK_ID POSITION CODEC DECODER ENGINE LABEL PURPOSE LEG_RENDERER <&3; do
    [ -n "$ORDINAL" ] || continue
    CURRENT_ORDINAL=$ORDINAL
    CURRENT_LABEL=$LABEL
    LEG_RENDERER=${LEG_RENDERER:-$MPV_VIDEO_OUTPUT}
    case "$LEG_RENDERER" in gpu-next|gpu) ;; *) fail invalid_leg_renderer ;; esac
    case "$LABEL" in ''|*[!A-Za-z0-9_-]*) fail invalid_manifest_label ;; esac
    cancelled && exit 0
    if [ "$PURPOSE" = battery-extra ]; then
        if [ "$EXTRA_DECISION" = undecided ]; then
            read_battery || fail supply_or_battery_probe_failed
            EXTRA_DECISION=skip
            if [ "$TARGET_SOFTWARE_OK" = true ] &&
               [ "$TARGET_SOFTWARE_FINISHED" -eq "${REQUIRED_TARGET_SOFTWARE_ROWS:-24}" ] && [ "$LEVEL" -gt 30 ]; then
                EXTRA_DECISION=run
            fi
            printf '{"batteryPercent":%s,"targetFinishedRows":%s,"targetDevicePlaybackChecksPassed":%s,"hostAuditPending":true,"entryThresholdExclusivePercent":30,"decision":"%s"}\n' \
                "$LEVEL" "$TARGET_SOFTWARE_FINISHED" "$TARGET_SOFTWARE_OK" "$EXTRA_DECISION" > "$BASE/decisions/battery-extra-entry.json" || fail extra_decision_write_failed
        fi
        if [ "$EXTRA_DECISION" = skip ]; then
            status leg_skipped extra_requires_completed_qualified_target_and_battery_above_30
            processed_rows=$((processed_rows+1))
            continue
        fi
    fi
    if [ "$HARD_STOP_UPTIME" -gt 0 ] && { [ "$PURPOSE" = optional-software ] || [ "$PURPOSE" = matrix ] || [ "$PURPOSE" = battery-extra ]; }; then
        if [ "$POSITION" -eq 1 ]; then
            SKIPPED_BUDGET_BLOCK=
            SKIPPED_BLOCK_REASON=
            block_remaining=$((HARD_STOP_UPTIME-$(uptime_s)))
            if [ "$block_remaining" -lt "$OPTIONAL_BLOCK_BUDGET_SECONDS" ]; then
                SKIPPED_BUDGET_BLOCK=$BLOCK_ID
                SKIPPED_BLOCK_REASON=insufficient_time_for_complete_block
                printf '{"block":"%s","remainingSeconds":%s,"requiredSeconds":%s,"decision":"skip_complete_block"}\n' \
                    "$BLOCK_ID" "$block_remaining" "$OPTIONAL_BLOCK_BUDGET_SECONDS" > "$BASE/decisions/block-$ORDINAL-deadline.json" || fail block_decision_write_failed
            fi
        fi
        if [ "$SKIPPED_BUDGET_BLOCK" = "$BLOCK_ID" ]; then
            status leg_skipped "$SKIPPED_BLOCK_REASON"
            processed_rows=$((processed_rows+1))
            continue
        fi
    fi
    if { [ "$PURPOSE" = optional-software ] || [ "$PURPOSE" = battery-extra ]; } && [ "$CODEC" = av1 ]; then
        if [ "$POSITION" -eq 1 ]; then
            block_engines=$(awk -F '\t' -v block="$BLOCK_ID" '$3==block {print $7}' "$BASE/manifest.tsv" | sort -u)
            for block_engine in $block_engines; do
                if [ ! -f "$BASE/decisions/software-av1-$block_engine-validated" ]; then
                    SKIPPED_BUDGET_BLOCK=$BLOCK_ID
                    SKIPPED_BLOCK_REASON=software_preflight_not_passed
                fi
            done
        fi
        if [ "$SKIPPED_BUDGET_BLOCK" = "$BLOCK_ID" ]; then
            status leg_skipped "$SKIPPED_BLOCK_REASON"
            processed_rows=$((processed_rows+1))
            continue
        fi
    fi
    if [ "$PURPOSE" = exo-conditional ] && [ -f "$BASE/decisions/exo-$CODEC-within-reference" ]; then
        status leg_skipped exo_screen_within_historical_observed_range
        processed_rows=$((processed_rows+1))
        continue
    fi
    if [ "$PURPOSE" = av1-conditional ]; then
        if [ "$AV1_DECISION" = undecided ]; then
            HARDWARE_FINISHED_EPOCH=$(date +%s)
            AV1_DECISION=skip
            [ "$HARDWARE_FINISHED_EPOCH" -lt "${AV1_BEFORE_EPOCH:-0}" ] && AV1_DECISION=run
            printf '{"hardwareFinishedEpoch":%s,"cutoffEpoch":%s,"decision":"%s"}\n' \
                "$HARDWARE_FINISHED_EPOCH" "${AV1_BEFORE_EPOCH:-0}" "$AV1_DECISION" > "$BASE/decisions/av1-decision.json" || fail av1_decision_write_failed
        fi
        if [ "$AV1_DECISION" = skip ]; then
            status leg_skipped hardware_finished_after_av1_cutoff
            processed_rows=$((processed_rows+1))
            continue
        fi
    fi
    stop_app || fail app_process_not_stopped
    mkdir "$BASE/control/$LABEL.claim" || fail result_label_already_claimed
    printf 'STOPPED\n' > "$BASE/control/$LABEL.process-before.txt" || fail process_evidence_failed
    for suffix in json jsonl; do
        presence=$(timed run-as "$PACKAGE" sh -c 'if test -e "$1"; then printf PRESENT; else printf ABSENT; fi' sh "files/power-results/$LABEL.$suffix") || fail result_presence_probe_failed
        [ "$presence" = ABSENT ] || fail result_already_exists
    done
    read_battery || fail supply_or_battery_probe_failed
    [ "$LEVEL" -ge "$MINIMUM_START_BATTERY" ] || { STOP_REASON=battery_below_start_threshold; exit 0; }
    # The legacy APK stays in use for comparable high-battery runs. The updated
    # guard is installed only when the old 25% guard could truncate a later leg.
    if [ "$LEVEL" -lt 28 ] && [ "$ACTIVE_TEST_SHA" = "$LEGACY_TEST_SHA" ] && [ "$LOW_TEST_SHA" != "$LEGACY_TEST_SHA" ]; then
        status installing_low_battery_test "$LEVEL"
        LOW_TEST_INSTALLED=1
        install_test "$LOW_TEST_APK" "$LOW_TEST_SHA" || fail low_battery_test_install_failed
        ACTIVE_TEST_SHA=$LOW_TEST_SHA
    fi
    while :; do
    cooldown_started=$(uptime_s)
    required_cooldown=$MINIMUM_COOLDOWN
    [ "$processed_rows" -ne 0 ] || required_cooldown=$FIRST_COOLDOWN_SECONDS
    previous_cooldown_temperature=
    stable_cooldown_polls=0
    status cooling "$LEVEL"
    while :; do
        cancelled && exit 0
        collection_window
        if [ "$COOLDOWN_INPUT_REFRESH" = true ]; then
            refresh_home || fail screen_lost_during_cooldown
        else
            screen_state || fail screen_lost_during_cooldown
        fi
        read_battery || fail supply_or_battery_probe_failed
        [ "$LEVEL" -ge "$MINIMUM_START_BATTERY" ] || { STOP_REASON=battery_below_start_threshold; exit 0; }
        if [ "$LEVEL" -lt 28 ] && [ "$ACTIVE_TEST_SHA" = "$LEGACY_TEST_SHA" ] && [ "$LOW_TEST_SHA" != "$LEGACY_TEST_SHA" ]; then
            status installing_low_battery_test "$LEVEL"
            LOW_TEST_INSTALLED=1
            install_test "$LOW_TEST_APK" "$LOW_TEST_SHA" || fail low_battery_test_install_failed
            ACTIVE_TEST_SHA=$LOW_TEST_SHA
            cooldown_started=$(uptime_s)
            continue
        fi
        cooldown_elapsed=$(( $(uptime_s) - cooldown_started ))
        printf '%s\t%s\t%s\t%s\n' "$(utc)" "$cooldown_elapsed" "$TEMP" "$LEVEL" >> "$BASE/control/$LABEL.cooling.tsv" || fail cooling_evidence_failed
        stable_temperature=true
        if [ "$COOLDOWN_REQUIRE_NONRISING" = true ]; then
            if [ -n "$previous_cooldown_temperature" ] && [ "$TEMP" -le "$previous_cooldown_temperature" ]; then
                stable_cooldown_polls=$((stable_cooldown_polls+1))
            else stable_cooldown_polls=0; fi
            [ "$stable_cooldown_polls" -ge "${COOLDOWN_STABLE_POLLS:-1}" ] || stable_temperature=false
        fi
        if [ "$cooldown_elapsed" -ge "$((required_cooldown+1))" ] &&
           [ "$TEMP" -le "$MAX_START_TEMPERATURE_TENTHS" ] && [ "$stable_temperature" = true ]; then break; fi
        previous_cooldown_temperature=$TEMP
        [ "$cooldown_elapsed" -lt 1200 ] || fail cooling_deadline_expired
        # A bounded wait also observes cancellation without depending on ADB.
        n=0
        while [ "$n" -lt "$COOLDOWN_POLL_SECONDS" ]; do
            cancelled && exit 0
            sleep 5
            n=$((n+5))
        done
    done
    refresh_home || fail screen_lost_before_playback
    timed cmd display set-brightness 0 || fail brightness_setup_failed
    read_battery || fail supply_or_battery_probe_failed
    [ "$TEMP" -le "$MAX_START_TEMPERATURE_TENTHS" ] || fail temperature_changed_before_playback
    [ "$LEVEL" -ge "$MINIMUM_START_BATTERY" ] || { STOP_REASON=battery_below_start_threshold; exit 0; }
    if [ "$LEVEL" -lt 28 ] && [ "$ACTIVE_TEST_SHA" = "$LEGACY_TEST_SHA" ] && [ "$LOW_TEST_SHA" != "$LEGACY_TEST_SHA" ]; then
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
    REQUIRE_HARDWARE=false
    [ "$HARDWARE_ONLY_AUTOMATIC" = true ] && [ "$DECODER" = automatic ] && REQUIRE_HARDWARE=true
    cancelled && exit 0
    LEG_WARMUP=$WARMUP_SECONDS; LEG_MEASURE=$MEASURE_SECONDS; LEG_VALIDATION=$VALIDATION_ONLY
    if [ "$PURPOSE" = software-preflight ]; then
        LEG_WARMUP=15; LEG_MEASURE=65; LEG_VALIDATION=true
    fi
    cancelled && exit 0
    instrumentation_timeout=480
    if [ "$HARD_STOP_UPTIME" -gt 0 ]; then
        remaining=$((HARD_STOP_UPTIME-$(uptime_s)))
        [ "$remaining" -gt "$((LEG_WARMUP+LEG_MEASURE+30))" ] || { STOP_REASON=night_deadline; exit 0; }
        [ "$instrumentation_timeout" -le "$remaining" ] || instrumentation_timeout=$remaining
    fi
    /system/bin/setsid /system/bin/toybox timeout --foreground -k 5 "$instrumentation_timeout" am instrument -w -r \
        -e powerBenchmark true -e engine "$ENGINE" -e fixture "$CODEC" \
        -e decoderMode "$DECODER" -e surfaceProtocol "$SURFACE_PROTOCOL" \
        -e mpvVideoOutput "$LEG_RENDERER" \
        -e requireHardwareDecoder "$REQUIRE_HARDWARE" -e frameProofMethod "$FRAME_PROOF_METHOD" \
        -e measurementCohort "$COHORT" -e sleepOnFailure false \
        -e validationOnly "$LEG_VALIDATION" -e allowExternalPower false \
        -e minimumBatteryPercent "$MINIMUM_SAMPLE_BATTERY" -e runLabel "$LABEL" \
        -e warmupSeconds "$LEG_WARMUP" -e measureSeconds "$LEG_MEASURE" \
        -e class io.github.joyelliot.zivplayer.PowerPlaybackBenchmarkTest \
        "$RUNNER" </dev/null > "$BASE/raw/$LABEL.instrumentation.log" 2>&1 &
    AM_PID=$!
    AM_START=$(capture_child_identity "$AM_PID") || fail instrumentation_process_identity_failed
    cat "/proc/$AM_PID/stat" > "$BASE/control/$LABEL.am-process-stat.txt" || fail process_evidence_failed
    cp "$BASE/control/$LABEL.am-process-stat.txt" "$BASE/active-am-process-stat.txt" || fail process_evidence_failed
    APP_PID_SEEN=
    command_timed_out=false
    while kill -0 "$AM_PID" 2>/dev/null; do
        cancelled && exit 0
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
        cancelled && exit 0
        sleep 2
    done
    timed run-as "$PACKAGE" cat "files/power-results/$LABEL.jsonl" > "$BASE/raw/$LABEL.jsonl" 2> "$BASE/raw/$LABEL.trace-error.txt" || :
    for boundary in start end; do
        timed run-as "$PACKAGE" cat "files/power-results/$LABEL-codec-$boundary.txt" > "$BASE/raw/$LABEL-codec-$boundary.txt" 2>/dev/null || :
    done
    timed cmd wifi status > "$BASE/control/$LABEL.wifi-after.txt" || :
    read_battery || fail supply_or_battery_probe_failed
    cp "$BASE/battery.current" "$BASE/control/$LABEL.battery-after.txt" || fail postflight_evidence_failed
    FRESH_PROCESS=false
    case "$APP_PID_SEEN" in ''|*[!0-9]*) APP_PID_SEEN=0 ;; *) FRESH_PROCESS=true ;; esac
    printf '{"ordinal":%s,"blockNumber":%s,"comparisonBlock":"%s","plannedOrdinal":%s,"codec":"%s","decoderMode":"%s","engine":"%s","runLabel":"%s","purpose":"%s","startedUtc":"%s","finishedUtc":"%s","startUptimeSeconds":%s,"endUptimeSeconds":%s,"exitCode":%s,"commandTimedOut":%s,"finalResultSeen":%s,"preflightBattery":{"plugged":false,"status":3,"level":%s,"temperatureTenthsC":%s,"voltageMv":%s},"afterBatteryPercent":%s,"cooldownSeconds":%s,"freshProcessVerified":%s,"newAppPid":%s,"screenActivityBeforeLeg":{"wakefulness":"Awake","unlocked":true,"lastUserActivity":%s},"temporaryScreenTimeoutMs":%s,"testApkSha256":"%s","networkStartBytes":%s,"networkEndBytes":%s,"networkMonitorAlive":%s}\n' \
        "$ORDINAL" "$BLOCK_NUMBER" "$BLOCK_ID" "$POSITION" "$CODEC" "$DECODER" "$ENGINE" "$LABEL" "$PURPOSE" \
        "$START_UTC" "$END_UTC" "$START_UPTIME" "$END_UPTIME" "$COMMAND_EXIT" "$command_timed_out" "$complete_result" \
        "$PRE_LEVEL" "$PRE_TEMP" "$PRE_VOLTAGE" "$LEVEL" "$cooldown_elapsed" "$FRESH_PROCESS" "$APP_PID_SEEN" "$PRE_ACTIVITY" "$SCREEN_TIMEOUT_MS" "$ACTIVE_TEST_SHA" \
        "$NETWORK_START" "$NETWORK_END" "$NETWORK_ALIVE" > "$BASE/control/$LABEL.control.json.tmp" || fail control_evidence_write_failed
    # Keep the frozen condition beside the full per-leg evidence without
    # changing the established control JSON format used by older sessions.
    printf '%s\n' "$LEG_RENDERER" > "$BASE/control/$LABEL.renderer.txt" || fail control_evidence_write_failed
    mv "$BASE/control/$LABEL.control.json.tmp" "$BASE/control/$LABEL.control.json" || fail control_evidence_move_failed
    if [ "$PURPOSE" = optional-software ]; then
        TARGET_SOFTWARE_FINISHED=$((TARGET_SOFTWARE_FINISHED+1))
        if [ "$complete_result" != true ] || [ "$COMMAND_EXIT" -ne 0 ] ||
           [ "$NETWORK_START" != "$NETWORK_END" ] || [ "$NETWORK_ALIVE" != true ] ||
           ! grep -q '"measurementQualified": true' "$BASE/raw/$LABEL.json" ||
           ! grep -q '"completed": true' "$BASE/raw/$LABEL.json" ||
           ! grep -q '"cleanupSucceeded": true' "$BASE/raw/$LABEL.json"; then
            TARGET_SOFTWARE_OK=false
        fi
    fi
    if [ "$PURPOSE" = software-preflight ] && [ "$complete_result" = true ] && [ "$COMMAND_EXIT" -eq 0 ] &&
        [ "$NETWORK_START" = "$NETWORK_END" ] && [ "$NETWORK_ALIVE" = true ] &&
        grep -q '"completed": true' "$BASE/raw/$LABEL.json" &&
        grep -q '"cleanupSucceeded": true' "$BASE/raw/$LABEL.json" &&
        grep -q '"playbackValidationPassed": true' "$BASE/raw/$LABEL.json" &&
        grep -q '"actualSoftwareDecoder": true' "$BASE/raw/$LABEL.json"; then
        printf '%s\n' "$LABEL" > "$BASE/decisions/software-$CODEC-$ENGINE-validated" || fail software_preflight_write_failed
    fi
    if [ "$PURPOSE" = exo-screen ] && [ "$complete_result" = true ] && [ "$COMMAND_EXIT" -eq 0 ] &&
        [ "$NETWORK_START" = "$NETWORK_END" ] && [ "$NETWORK_ALIVE" = true ] &&
        grep -q '"measurementQualified": true' "$BASE/raw/$LABEL.json" &&
        grep -q '"actualSoftwareDecoder": false' "$BASE/raw/$LABEL.json"; then
        SCREEN_POWER=$(awk -F: '/"steadyMeanPowerMw":/ {gsub(/[,[:space:]]/, "", $2); print $2}' "$BASE/raw/$LABEL.json")
        if awk -F '\t' -v codec="$CODEC" -v power="$SCREEN_POWER" '
            $1==codec {seen=1; ok=(power!="" && power+0 >= $2 && power+0 <= $3)} END {exit !(seen && ok)}' "$BASE/exo-reference.tsv"; then
            printf '%s\n' "$SCREEN_POWER" > "$BASE/decisions/exo-$CODEC-within-reference" || fail exo_screen_write_failed
        fi
    fi
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
