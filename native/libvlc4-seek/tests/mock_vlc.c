// SPDX-License-Identifier: GPL-3.0-or-later
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
static void (*seek_callback)(const void *, void *);
static void *callback_data;
static int refs;
static bool reject;
void libvlc_media_player_retain(void *player) { assert(player); ++refs; }
void libvlc_media_player_release(void *player) { assert(player); --refs; }
int libvlc_media_player_watch_time(void *player, int64_t period, void (*update)(const void *, void *),
        void (*pause)(int64_t, void *), void (*seek)(const void *, void *), void *data) {
    assert(player && period == 100000 && update && !pause && seek);
    if (seek_callback) return -1;
    seek_callback = seek; callback_data = data; return 0;
}
void libvlc_media_player_unwatch_time(void *player) { assert(player); seek_callback = 0; callback_data = 0; }
int libvlc_media_player_set_time(void *player, int64_t time, bool fast) {
    assert(player && time >= 0); (void)fast; return reject ? -1 : 0;
}
void mock_seek_event(bool start) { if (seek_callback) seek_callback(start ? (void *)1 : 0, callback_data); }
void mock_reject(bool value) { reject = value; }
int mock_refs(void) { return refs; }
