// SPDX-License-Identifier: GPL-3.0-or-later
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
static void (*seek_callback)(const void *, void *);
static void (*update_callback)(const void *, void *);
static void (*pause_callback)(int64_t, void *);
static int64_t now = 2000000;
static void *callback_data;
static int refs;
static bool reject;
static int64_t seek_timestamp;
void *libvlc_media_player_retain(void *player) { assert(player); ++refs; return player; }
void libvlc_media_player_release(void *player) { assert(player); --refs; }
int libvlc_media_player_watch_time(void *player, int64_t period, void (*update)(const void *, void *),
        void (*pause)(int64_t, void *), void (*seek)(const void *, void *), void *data) {
    assert(player && period == 100000 && update && pause && seek);
    if (seek_callback) return -1;
    seek_callback = seek; update_callback = update; pause_callback = pause; callback_data = data; return 0;
}
void libvlc_media_player_unwatch_time(void *player) {
    assert(player); seek_callback = 0; update_callback = 0; pause_callback = 0; callback_data = 0;
}
typedef struct { double position, rate; int64_t ts_us, length_us, system_date_us; } mock_point;
int64_t libvlc_clock(void) { return now; }
int libvlc_media_player_time_point_interpolate(const mock_point *point, int64_t date, int64_t *timestamp, double *position) {
    (void)position;
    *timestamp = point->ts_us + (point->system_date_us == INT64_MAX ? 0 : date - point->system_date_us);
    return *timestamp < 0 ? -1 : 0;
}
void mock_time_update(int64_t timestamp, bool paused) {
    mock_point point = { .rate = 1, .ts_us = timestamp, .system_date_us = paused ? INT64_MAX : now };
    if (update_callback) update_callback(&point, callback_data);
}
void mock_pause(int64_t date) { if (pause_callback) pause_callback(date, callback_data); }
void mock_clock(int64_t date) { now = date; }
int libvlc_media_player_set_time(void *player, int64_t time, bool fast) {
    assert(player && time >= 0); (void)fast;
    if (!reject) seek_timestamp = time * 1000;
    return reject ? -1 : 0;
}
void mock_seek_event(bool start) {
    mock_point point = { .rate = 1, .ts_us = seek_timestamp, .system_date_us = INT64_MAX };
    if (seek_callback) seek_callback(start ? &point : 0, callback_data);
}
void mock_reject(bool value) { reject = value; }
int mock_refs(void) { return refs; }
