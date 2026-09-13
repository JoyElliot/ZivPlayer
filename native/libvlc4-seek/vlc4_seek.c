// SPDX-License-Identifier: GPL-3.0-or-later
// Public LibVLC 4 ABI, pinned to core c84ef1eda4f14f8b57a5b266b51c843cf51ed28b.
// No private VLC/JNI structures are accessed. getInstance() returns the public player pointer.
#include <jni.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <dlfcn.h>
#include <pthread.h>

typedef struct libvlc_media_player_t vlc_player;
typedef struct libvlc_media_player_time_point_t vlc_time_point;
typedef void (*update_cb)(const vlc_time_point *, void *);
typedef void (*pause_cb)(int64_t, void *);
typedef void (*seek_cb)(const vlc_time_point *, void *);
typedef struct {
    JavaVM *vm;
    jobject observer;
    jmethodID completed;
    void *library;
    vlc_player *player;
    void (*unwatch)(vlc_player *);
    void (*release)(vlc_player *);
    int (*set_time)(vlc_player *, int64_t, bool);
    pthread_mutex_t mutex;
    int64_t token;
    bool started;
} bridge;

static void time_updated(const vlc_time_point *point, void *opaque) {
    (void)point; (void)opaque;
}

static void seek_changed(const vlc_time_point *point, void *opaque) {
    bridge *b = opaque;
    int64_t completed = 0;
    pthread_mutex_lock(&b->mutex);
    if (point != NULL) b->started = b->token != 0;
    else if (b->started) {
        completed = b->token;
        b->token = 0;
        b->started = false;
    }
    pthread_mutex_unlock(&b->mutex);
    if (!completed) return;
    // VLC forbids player calls inside timer callbacks. Java only posts to its main Handler.
    JNIEnv *env = NULL;
    bool attached = (*b->vm)->GetEnv(b->vm, (void **)&env, JNI_VERSION_1_6) == JNI_EDETACHED;
    if (attached && (*b->vm)->AttachCurrentThread(b->vm, &env, NULL) != JNI_OK) return;
    if (env != NULL) {
        (*env)->CallVoidMethod(env, b->observer, b->completed, (jlong)completed);
        if ((*env)->ExceptionCheck(env)) (*env)->ExceptionClear(env);
    }
    if (attached) (*b->vm)->DetachCurrentThread(b->vm);
}

JNIEXPORT jlong JNICALL
Java_io_github_joyelliot_zivplayer_platform_libvlc_VlcSeekBridge_nativeOpen(JNIEnv *env, jobject self, jlong pointer) {
    if (!pointer) return 0;
    bridge *b = calloc(1, sizeof(*b));
    if (!b) return 0;
    b->library = dlopen("libvlc.so", RTLD_NOW | RTLD_LOCAL);
    if (!b->library) { free(b); return 0; }
    int (*watch)(vlc_player *, int64_t, update_cb, pause_cb, seek_cb, void *) =
        dlsym(b->library, "libvlc_media_player_watch_time");
    void (*retain)(vlc_player *) = dlsym(b->library, "libvlc_media_player_retain");
    b->unwatch = dlsym(b->library, "libvlc_media_player_unwatch_time");
    b->release = dlsym(b->library, "libvlc_media_player_release");
    b->set_time = dlsym(b->library, "libvlc_media_player_set_time");
    jclass clazz = (*env)->GetObjectClass(env, self);
    b->completed = (*env)->GetMethodID(env, clazz, "onNativeSeekCompleted", "(J)V");
    (*env)->DeleteLocalRef(env, clazz);
    if (!watch || !retain || !b->unwatch || !b->release || !b->set_time || !b->completed) {
        dlclose(b->library); free(b); return 0;
    }
    b->observer = (*env)->NewGlobalRef(env, self);
    if (!b->observer) { dlclose(b->library); free(b); return 0; }
    (*env)->GetJavaVM(env, &b->vm);
    b->player = (vlc_player *)(intptr_t)pointer;
    pthread_mutex_init(&b->mutex, NULL);
    retain(b->player);
    if (watch(b->player, 100000, time_updated, NULL, seek_changed, b) != 0) {
        b->release(b->player);
        pthread_mutex_destroy(&b->mutex);
        (*env)->DeleteGlobalRef(env, b->observer);
        dlclose(b->library); free(b); return 0;
    }
    return (jlong)(intptr_t)b;
}

JNIEXPORT jint JNICALL
Java_io_github_joyelliot_zivplayer_platform_libvlc_VlcSeekBridge_nativeSeek(JNIEnv *env, jobject self,
        jlong handle, jlong token, jlong milliseconds, jboolean fast) {
    (void)env; (void)self;
    bridge *b = (bridge *)(intptr_t)handle;
    if (!b || !token || milliseconds < 0) return -1;
    pthread_mutex_lock(&b->mutex);
    if (b->token) { pthread_mutex_unlock(&b->mutex); return -1; }
    b->token = token;
    b->started = false;
    pthread_mutex_unlock(&b->mutex);
    int result = b->set_time(b->player, milliseconds, fast == JNI_TRUE);
    if (result != 0) {
        pthread_mutex_lock(&b->mutex);
        b->token = 0;
        b->started = false;
        pthread_mutex_unlock(&b->mutex);
    }
    return result;
}

JNIEXPORT void JNICALL
Java_io_github_joyelliot_zivplayer_platform_libvlc_VlcSeekBridge_nativeClose(JNIEnv *env, jobject self, jlong handle) {
    (void)self;
    bridge *b = (bridge *)(intptr_t)handle;
    if (!b) return;
    // unwatch fences in-progress callbacks before deleting the observer and freeing state.
    b->unwatch(b->player);
    b->release(b->player);
    (*env)->DeleteGlobalRef(env, b->observer);
    pthread_mutex_destroy(&b->mutex);
    dlclose(b->library);
    free(b);
}
