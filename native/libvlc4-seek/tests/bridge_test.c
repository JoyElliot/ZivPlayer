// SPDX-License-Identifier: GPL-3.0-or-later
// Exercises the actual JNI C implementation against a deterministic public-API fixture.
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../vlc4_seek.c"
extern void mock_seek_event(bool);
extern void mock_reject(bool);
extern int mock_refs(void);
static JavaVM vm;
static JNIEnv env;
static pthread_t main_thread;
static int global_refs, attachments, detachments, callback_count;
static jlong last_token;
static jclass get_class(JNIEnv *e, jobject o) { (void)e; return (jclass)o; }
static jmethodID get_method(JNIEnv *e, jclass c, const char *name, const char *signature) {
    (void)e; (void)c; assert(!strcmp(name, "onNativeSeekCompleted") && !strcmp(signature, "(J)V"));
    return (jmethodID)1;
}
static void delete_local(JNIEnv *e, jobject o) { (void)e; (void)o; }
static jobject new_global(JNIEnv *e, jobject o) { (void)e; ++global_refs; return o; }
static void delete_global(JNIEnv *e, jobject o) { (void)e; (void)o; --global_refs; }
static jint get_vm(JNIEnv *e, JavaVM **v) { (void)e; *v = &vm; return JNI_OK; }
static jint get_env(JavaVM *v, void **e, jint version) {
    (void)v; assert(version == JNI_VERSION_1_6);
    if (!pthread_equal(main_thread, pthread_self())) return JNI_EDETACHED;
    *e = &env; return JNI_OK;
}
static jint attach(JavaVM *v, JNIEnv **e, void *args) { (void)v; (void)args; ++attachments; *e = &env; return JNI_OK; }
static jint detach(JavaVM *v) { (void)v; ++detachments; return JNI_OK; }
static void completed(JNIEnv *e, jobject o, jmethodID m, ...) {
    (void)e; (void)o; (void)m;
    va_list args; va_start(args, m); last_token = va_arg(args, jlong); va_end(args); ++callback_count;
}
static jboolean no_exception(JNIEnv *e) { (void)e; return JNI_FALSE; }
static void clear_exception(JNIEnv *e) { (void)e; assert(false); }
static const struct JNINativeInterface jni = {
    .GetObjectClass = get_class, .GetMethodID = get_method, .DeleteLocalRef = delete_local,
    .NewGlobalRef = new_global, .DeleteGlobalRef = delete_global, .GetJavaVM = get_vm,
    .CallVoidMethod = completed, .ExceptionCheck = no_exception, .ExceptionClear = clear_exception,
};
static const struct JNIInvokeInterface invoke = { .GetEnv = get_env, .AttachCurrentThread = attach, .DetachCurrentThread = detach };
#define OPEN Java_io_github_joyelliot_zivplayer_platform_libvlc_VlcSeekBridge_nativeOpen
#define SEEK Java_io_github_joyelliot_zivplayer_platform_libvlc_VlcSeekBridge_nativeSeek
#define CLOSE Java_io_github_joyelliot_zivplayer_platform_libvlc_VlcSeekBridge_nativeClose
static void *complete_on_native_thread(void *unused) { (void)unused; mock_seek_event(false); return NULL; }
int main(void) {
    env = &jni; vm = &invoke; main_thread = pthread_self();
    jobject observer = (jobject)1;
    assert(OPEN(&env, observer, 0) == 0);
    jlong h = OPEN(&env, observer, 1);
    assert(h && global_refs == 1 && mock_refs() == 1);
    assert(OPEN(&env, observer, 1) == 0 && global_refs == 1 && mock_refs() == 1);
    mock_seek_event(true); mock_seek_event(false);
    assert(callback_count == 0); // Unarmed startup seeks are ignored.
    assert(SEEK(&env, observer, h, 10, 1000, JNI_TRUE) == 0);
    assert(SEEK(&env, observer, h, 11, 2000, JNI_TRUE) == -1); // No unfinished replacement.
    mock_seek_event(false); assert(callback_count == 0); // Completion needs a matching start.
    mock_seek_event(true); mock_seek_event(false);
    assert(callback_count == 1 && last_token == 10);
    mock_seek_event(false); assert(callback_count == 1); // Duplicate completion is ignored.
    mock_reject(true); assert(SEEK(&env, observer, h, 12, 3000, JNI_FALSE) == -1);
    mock_seek_event(true); mock_seek_event(false); assert(callback_count == 1);
    mock_reject(false); assert(SEEK(&env, observer, h, 13, 4000, JNI_FALSE) == 0);
    mock_seek_event(true);
    pthread_t thread; assert(!pthread_create(&thread, NULL, complete_on_native_thread, NULL));
    assert(!pthread_join(thread, NULL));
    assert(callback_count == 2 && last_token == 13 && attachments == 1 && detachments == 1);
    assert(SEEK(&env, observer, h, 14, 5000, JNI_FALSE) == 0); mock_seek_event(true);
    CLOSE(&env, observer, h); mock_seek_event(false);
    assert(callback_count == 2 && global_refs == 0 && mock_refs() == 0);
    h = OPEN(&env, observer, 1); assert(h); CLOSE(&env, observer, h);
    assert(global_refs == 0 && mock_refs() == 0);
    puts("PASS: JNI watcher ownership, start/end fences, rejection, native-thread delivery, close and reopen");
}
