// SPDX-License-Identifier: GPL-3.0-or-later

#include <jni.h>

extern "C" {
#include <libavcodec/jni.h>
#include <mpv/client.h>
}

#include <climits>
#include <clocale>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <exception>
#include <memory>
#include <mutex>
#include <new>
#include <string>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {

constexpr char kBindingsClass[] =
        "io/github/joyelliot/zivplayer/platform/libmpv/MpvNativeBindings";
constexpr char kWidOption[] = "wid";

constexpr int kIntegerFieldCount = 8;
constexpr int kLongFieldCount = 4;
constexpr int kDoubleFieldCount = 1;
constexpr int kStringFieldCount = 2;

enum IntegerField : int {
    kEventId = 0,
    kEventError = 1,
    kPropertyFormat = 2,
    kEndReason = 3,
    kEndError = 4,
    kInsertCount = 5,
    kPresenceFlags = 6,
    kPropertyFlag = 7,
};

enum LongField : int {
    kReplyUserdata = 0,
    kPlaylistEntryId = 1,
    kPlaylistInsertId = 2,
    kPropertyInt64 = 3,
};

enum DoubleField : int {
    kPropertyDouble = 0,
};

enum StringField : int {
    kPropertyName = 0,
    kPropertyString = 1,
};

enum PresenceFlag : int {
    kHasReplyUserdata = 1 << 0,
    kHasPlaylistEntryId = 1 << 1,
    kHasEndReason = 1 << 2,
    kHasPlaylistInsertId = 1 << 3,
    kHasPropertyValue = 1 << 4,
};

struct Client {
    explicit Client(mpv_handle* value) : handle(value) {}

    // Lifecycle state only. Never hold this mutex across a libmpv call.
    std::mutex mutex;
    std::condition_variable idle;
    std::mutex initialization_mutex;
    std::mutex surface_mutex;
    mpv_handle* handle;
    jobject current_surface = nullptr;
    std::vector<jobject> retained_surfaces;
    std::size_t active_calls = 0;
    bool initialized = false;
    bool closing = false;
    bool waiting = false;
    bool destroy_in_progress = false;
    bool destroyed = false;
    std::thread::id wait_thread;
};

std::mutex g_registry_mutex;
std::unordered_map<jlong, std::shared_ptr<Client>> g_registry;
std::uint64_t g_next_token = 1;

std::mutex g_process_context_mutex;
jobject g_process_context = nullptr;
std::once_flag g_locale_once;
bool g_locale_ready = false;

void ThrowByName(JNIEnv* env, const char* class_name, const char* message) {
    if (env->ExceptionCheck()) {
        return;
    }
    jclass type = env->FindClass(class_name);
    if (type == nullptr) {
        return;
    }
    env->ThrowNew(type, message);
    env->DeleteLocalRef(type);
}

void ThrowIllegalArgument(JNIEnv* env, const char* message) {
    ThrowByName(env, "java/lang/IllegalArgumentException", message);
}

void ThrowIllegalState(JNIEnv* env, const char* message) {
    ThrowByName(env, "java/lang/IllegalStateException", message);
}

void ThrowOutOfMemory(JNIEnv* env) {
    ThrowByName(env, "java/lang/OutOfMemoryError", "Native allocation failed.");
}

void ThrowUnexpectedNativeFailure(JNIEnv* env) {
    ThrowIllegalState(env, "The native libmpv bridge failed unexpectedly.");
}

template <typename Function>
jint GuardStatus(JNIEnv* env, Function function) noexcept {
    try {
        return function();
    } catch (const std::bad_alloc&) {
        ThrowOutOfMemory(env);
        return MPV_ERROR_NOMEM;
    } catch (const std::exception&) {
        ThrowUnexpectedNativeFailure(env);
        return MPV_ERROR_GENERIC;
    } catch (...) {
        ThrowUnexpectedNativeFailure(env);
        return MPV_ERROR_GENERIC;
    }
}

template <typename Function>
jlong GuardToken(JNIEnv* env, Function function) noexcept {
    try {
        return function();
    } catch (const std::bad_alloc&) {
        ThrowOutOfMemory(env);
        return 0;
    } catch (const std::exception&) {
        ThrowUnexpectedNativeFailure(env);
        return 0;
    } catch (...) {
        ThrowUnexpectedNativeFailure(env);
        return 0;
    }
}

bool EncodeCodePoint(std::uint32_t code_point, std::string* output) {
    if (code_point == 0) {
        return false;
    }
    if (code_point <= 0x7f) {
        output->push_back(static_cast<char>(code_point));
    } else if (code_point <= 0x7ff) {
        output->push_back(static_cast<char>(0xc0 | (code_point >> 6)));
        output->push_back(static_cast<char>(0x80 | (code_point & 0x3f)));
    } else if (code_point <= 0xffff) {
        output->push_back(static_cast<char>(0xe0 | (code_point >> 12)));
        output->push_back(static_cast<char>(0x80 | ((code_point >> 6) & 0x3f)));
        output->push_back(static_cast<char>(0x80 | (code_point & 0x3f)));
    } else {
        output->push_back(static_cast<char>(0xf0 | (code_point >> 18)));
        output->push_back(static_cast<char>(0x80 | ((code_point >> 12) & 0x3f)));
        output->push_back(static_cast<char>(0x80 | ((code_point >> 6) & 0x3f)));
        output->push_back(static_cast<char>(0x80 | (code_point & 0x3f)));
    }
    return true;
}

bool JStringToUtf8(JNIEnv* env, jstring input, std::string* output) {
    if (input == nullptr) {
        ThrowIllegalArgument(env, "A native string argument was null.");
        return false;
    }

    const jsize length = env->GetStringLength(input);
    const jchar* characters = env->GetStringChars(input, nullptr);
    if (characters == nullptr) {
        return false;
    }

    bool valid = true;
    try {
        output->clear();
        output->reserve(static_cast<std::size_t>(length) * 3U);
        for (jsize index = 0; index < length; ++index) {
            std::uint32_t code_point = characters[index];
            if (code_point >= 0xd800 && code_point <= 0xdbff) {
                if (index + 1 >= length) {
                    valid = false;
                    break;
                }
                const std::uint32_t low = characters[++index];
                if (low < 0xdc00 || low > 0xdfff) {
                    valid = false;
                    break;
                }
                code_point = 0x10000U + ((code_point - 0xd800U) << 10U) +
                        (low - 0xdc00U);
            } else if (code_point >= 0xdc00 && code_point <= 0xdfff) {
                valid = false;
                break;
            }
            if (!EncodeCodePoint(code_point, output)) {
                valid = false;
                break;
            }
        }
    } catch (const std::bad_alloc&) {
        env->ReleaseStringChars(input, characters);
        ThrowOutOfMemory(env);
        return false;
    }
    env->ReleaseStringChars(input, characters);

    if (!valid) {
        ThrowIllegalArgument(
                env,
                "A native string contained NUL or an unpaired UTF-16 surrogate.");
        return false;
    }
    return true;
}

bool IsContinuation(std::uint8_t byte) {
    return (byte & 0xc0U) == 0x80U;
}

jstring Utf8ToJString(JNIEnv* env, const char* input) {
    if (input == nullptr) {
        return nullptr;
    }

    const auto* bytes = reinterpret_cast<const std::uint8_t*>(input);
    const std::size_t length = std::strlen(input);
    std::vector<jchar> characters;
    try {
        characters.reserve(length);
        std::size_t index = 0;
        while (index < length) {
            const std::uint8_t first = bytes[index];
            std::uint32_t code_point = 0xfffd;
            std::size_t consumed = 1;
            if (first <= 0x7f) {
                code_point = first;
            } else if (
                    first >= 0xc2 && first <= 0xdf && index + 1 < length &&
                    IsContinuation(bytes[index + 1])) {
                code_point = ((first & 0x1fU) << 6U) | (bytes[index + 1] & 0x3fU);
                consumed = 2;
            } else if (
                    first >= 0xe0 && first <= 0xef && index + 2 < length &&
                    IsContinuation(bytes[index + 1]) && IsContinuation(bytes[index + 2]) &&
                    !(first == 0xe0 && bytes[index + 1] < 0xa0) &&
                    !(first == 0xed && bytes[index + 1] >= 0xa0)) {
                code_point = ((first & 0x0fU) << 12U) |
                        ((bytes[index + 1] & 0x3fU) << 6U) |
                        (bytes[index + 2] & 0x3fU);
                consumed = 3;
            } else if (
                    first >= 0xf0 && first <= 0xf4 && index + 3 < length &&
                    IsContinuation(bytes[index + 1]) && IsContinuation(bytes[index + 2]) &&
                    IsContinuation(bytes[index + 3]) &&
                    !(first == 0xf0 && bytes[index + 1] < 0x90) &&
                    !(first == 0xf4 && bytes[index + 1] >= 0x90)) {
                code_point = ((first & 0x07U) << 18U) |
                        ((bytes[index + 1] & 0x3fU) << 12U) |
                        ((bytes[index + 2] & 0x3fU) << 6U) |
                        (bytes[index + 3] & 0x3fU);
                consumed = 4;
            }

            if (code_point <= 0xffff) {
                characters.push_back(static_cast<jchar>(code_point));
            } else {
                code_point -= 0x10000U;
                characters.push_back(static_cast<jchar>(0xd800U + (code_point >> 10U)));
                characters.push_back(static_cast<jchar>(0xdc00U + (code_point & 0x3ffU)));
            }
            index += consumed;
        }
    } catch (const std::bad_alloc&) {
        ThrowOutOfMemory(env);
        return nullptr;
    }

    if (characters.size() > static_cast<std::size_t>(INT_MAX)) {
        ThrowOutOfMemory(env);
        return nullptr;
    }
    return env->NewString(
            characters.empty() ? nullptr : characters.data(),
            static_cast<jsize>(characters.size()));
}

std::shared_ptr<Client> LookupClient(jlong token) {
    if (token <= 0) {
        return nullptr;
    }
    std::lock_guard<std::mutex> lock(g_registry_mutex);
    const auto found = g_registry.find(token);
    return found == g_registry.end() ? nullptr : found->second;
}

class ClientCall {
  public:
    ClientCall(jlong token, bool allow_closing, bool wait_call = false)
        : client_(LookupClient(token)), wait_call_(wait_call) {
        if (client_ == nullptr) {
            return;
        }
        std::lock_guard<std::mutex> lock(client_->mutex);
        if (
                client_->handle == nullptr || client_->destroyed ||
                (client_->closing && (!allow_closing || client_->destroy_in_progress)) ||
                (wait_call_ && client_->waiting)) {
            client_.reset();
            return;
        }
        ++client_->active_calls;
        if (wait_call_) {
            client_->waiting = true;
            client_->wait_thread = std::this_thread::get_id();
        }
        handle_ = client_->handle;
    }

    ClientCall(const ClientCall&) = delete;
    ClientCall& operator=(const ClientCall&) = delete;

    ~ClientCall() {
        if (client_ == nullptr) {
            return;
        }
        std::lock_guard<std::mutex> lock(client_->mutex);
        if (wait_call_) {
            client_->waiting = false;
            client_->wait_thread = std::thread::id();
        }
        --client_->active_calls;
        client_->idle.notify_all();
    }

    bool valid() const { return client_ != nullptr; }
    mpv_handle* handle() const { return handle_; }
    const std::shared_ptr<Client>& client() const { return client_; }

  private:
    std::shared_ptr<Client> client_;
    mpv_handle* handle_ = nullptr;
    bool wait_call_ = false;
};

bool EnsureArrayLength(JNIEnv* env, jarray value, jsize expected, const char* message) {
    if (value == nullptr || env->GetArrayLength(value) != expected) {
        ThrowIllegalArgument(env, message);
        return false;
    }
    return true;
}

bool EnsureProcessContext(JNIEnv* env, jobject application_context) {
    if (application_context == nullptr) {
        ThrowIllegalArgument(env, "The Android application context was null.");
        return false;
    }
    std::lock_guard<std::mutex> lock(g_process_context_mutex);
    if (g_process_context != nullptr) {
        return true;
    }

    jobject global_context = env->NewGlobalRef(application_context);
    if (global_context == nullptr) {
        return false;
    }
    const int status = av_jni_set_android_app_ctx(global_context, nullptr);
    if (status < 0) {
        env->DeleteGlobalRef(global_context);
        ThrowIllegalState(env, "FFmpeg rejected the Android application context.");
        return false;
    }
    g_process_context = global_context;
    return true;
}

jlong AllocateToken(JNIEnv* env, const std::shared_ptr<Client>& client) {
    std::lock_guard<std::mutex> lock(g_registry_mutex);
    if (g_next_token == 0 || g_next_token > static_cast<std::uint64_t>(LLONG_MAX)) {
        return 0;
    }
    const jlong token = static_cast<jlong>(g_next_token++);
    try {
        g_registry.emplace(token, client);
    } catch (const std::bad_alloc&) {
        ThrowOutOfMemory(env);
        return 0;
    }
    return token;
}

jlong NativeCreate(JNIEnv* env, jobject, jobject application_context) {
    std::call_once(g_locale_once, [] {
        g_locale_ready = std::setlocale(LC_NUMERIC, "C") != nullptr;
    });
    if (!g_locale_ready) {
        ThrowIllegalState(env, "LC_NUMERIC could not be set to C before mpv_create.");
        return 0;
    }
    if (!EnsureProcessContext(env, application_context)) {
        return 0;
    }

    mpv_handle* handle = mpv_create();
    if (handle == nullptr) {
        return 0;
    }

    std::shared_ptr<Client> client;
    try {
        client = std::make_shared<Client>(handle);
    } catch (const std::bad_alloc&) {
        mpv_terminate_destroy(handle);
        ThrowOutOfMemory(env);
        return 0;
    }
    const jlong token = AllocateToken(env, client);
    if (token == 0) {
        mpv_terminate_destroy(handle);
        if (!env->ExceptionCheck()) {
            ThrowIllegalState(env, "The native client token space is exhausted.");
        }
    }
    return token;
}

jint NativeInitialize(JNIEnv*, jobject, jlong token) {
    ClientCall call(token, false);
    if (!call.valid()) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    std::lock_guard<std::mutex> lock(call.client()->initialization_mutex);
    if (call.client()->initialized) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    const int status = mpv_initialize(call.handle());
    if (status >= 0) {
        call.client()->initialized = true;
    }
    return status;
}

jint NativeSetOptionString(
        JNIEnv* env,
        jobject,
        jlong token,
        jstring name,
        jstring value) {
    std::string native_name;
    std::string native_value;
    if (!JStringToUtf8(env, name, &native_name) ||
        !JStringToUtf8(env, value, &native_value)) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    ClientCall call(token, false);
    if (!call.valid()) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    std::lock_guard<std::mutex> lock(call.client()->initialization_mutex);
    if (call.client()->initialized) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    return mpv_set_option_string(call.handle(), native_name.c_str(), native_value.c_str());
}

jint NativeCommand(JNIEnv* env, jobject, jlong token, jobjectArray arguments) {
    if (arguments == nullptr) {
        ThrowIllegalArgument(env, "The command argument array was null.");
        return MPV_ERROR_INVALID_PARAMETER;
    }
    const jsize count = env->GetArrayLength(arguments);
    if (count <= 0) {
        ThrowIllegalArgument(env, "The command argument array was empty.");
        return MPV_ERROR_INVALID_PARAMETER;
    }

    std::vector<std::string> values;
    std::vector<const char*> pointers;
    try {
        values.reserve(static_cast<std::size_t>(count));
        for (jsize index = 0; index < count; ++index) {
            auto value = static_cast<jstring>(env->GetObjectArrayElement(arguments, index));
            if (env->ExceptionCheck()) {
                return MPV_ERROR_INVALID_PARAMETER;
            }
            std::string native_value;
            const bool converted = JStringToUtf8(env, value, &native_value);
            if (value != nullptr) {
                env->DeleteLocalRef(value);
            }
            if (!converted) {
                return MPV_ERROR_INVALID_PARAMETER;
            }
            values.push_back(std::move(native_value));
        }
        pointers.reserve(values.size() + 1U);
        for (const auto& value : values) {
            pointers.push_back(value.c_str());
        }
        pointers.push_back(nullptr);
    } catch (const std::bad_alloc&) {
        ThrowOutOfMemory(env);
        return MPV_ERROR_NOMEM;
    }

    ClientCall call(token, false);
    return call.valid() ? mpv_command(call.handle(), pointers.data())
                        : MPV_ERROR_INVALID_PARAMETER;
}

jint NativeGetPropertyDouble(
        JNIEnv* env,
        jobject,
        jlong token,
        jstring name,
        jdoubleArray output) {
    if (!EnsureArrayLength(env, output, 1, "The double output buffer is malformed.")) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    std::string native_name;
    if (!JStringToUtf8(env, name, &native_name)) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    ClientCall call(token, false);
    if (!call.valid()) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    double value = 0.0;
    const int status = mpv_get_property(
            call.handle(), native_name.c_str(), MPV_FORMAT_DOUBLE, &value);
    if (status >= 0) {
        const jdouble result = value;
        env->SetDoubleArrayRegion(output, 0, 1, &result);
    }
    return status;
}

jint NativeGetPropertyBoolean(
        JNIEnv* env,
        jobject,
        jlong token,
        jstring name,
        jintArray output) {
    if (!EnsureArrayLength(env, output, 1, "The boolean output buffer is malformed.")) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    std::string native_name;
    if (!JStringToUtf8(env, name, &native_name)) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    ClientCall call(token, false);
    if (!call.valid()) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    int value = 0;
    const int status = mpv_get_property(
            call.handle(), native_name.c_str(), MPV_FORMAT_FLAG, &value);
    if (status >= 0) {
        const jint result = value == 0 ? 0 : 1;
        env->SetIntArrayRegion(output, 0, 1, &result);
    }
    return status;
}

jint NativeSetPropertyDouble(
        JNIEnv* env,
        jobject,
        jlong token,
        jstring name,
        jdouble value) {
    std::string native_name;
    if (!JStringToUtf8(env, name, &native_name)) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    ClientCall call(token, false);
    double native_value = value;
    return call.valid()
            ? mpv_set_property(
                      call.handle(), native_name.c_str(), MPV_FORMAT_DOUBLE, &native_value)
            : MPV_ERROR_INVALID_PARAMETER;
}

jint NativeSetPropertyBoolean(
        JNIEnv* env,
        jobject,
        jlong token,
        jstring name,
        jboolean value) {
    std::string native_name;
    if (!JStringToUtf8(env, name, &native_name)) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    ClientCall call(token, false);
    int native_value = value == JNI_FALSE ? 0 : 1;
    return call.valid()
            ? mpv_set_property(
                      call.handle(), native_name.c_str(), MPV_FORMAT_FLAG, &native_value)
            : MPV_ERROR_INVALID_PARAMETER;
}

jint NativeSetPropertyString(
        JNIEnv* env,
        jobject,
        jlong token,
        jstring name,
        jstring value) {
    std::string native_name;
    std::string native_value;
    if (!JStringToUtf8(env, name, &native_name) ||
        !JStringToUtf8(env, value, &native_value)) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    ClientCall call(token, false);
    return call.valid()
            ? mpv_set_property_string(
                      call.handle(), native_name.c_str(), native_value.c_str())
            : MPV_ERROR_INVALID_PARAMETER;
}

jint NativeObserveProperty(
        JNIEnv* env,
        jobject,
        jlong token,
        jstring name,
        jint raw_format,
        jlong reply_userdata) {
    if (reply_userdata <= 0) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    std::string native_name;
    if (!JStringToUtf8(env, name, &native_name)) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    ClientCall call(token, false);
    return call.valid()
            ? mpv_observe_property(
                      call.handle(),
                      static_cast<std::uint64_t>(reply_userdata),
                      native_name.c_str(),
                      static_cast<mpv_format>(raw_format))
            : MPV_ERROR_INVALID_PARAMETER;
}

jint NativeUnobserveProperty(JNIEnv*, jobject, jlong token, jlong reply_userdata) {
    if (reply_userdata <= 0) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    ClientCall call(token, false);
    return call.valid()
            ? mpv_unobserve_property(
                      call.handle(), static_cast<std::uint64_t>(reply_userdata))
            : MPV_ERROR_INVALID_PARAMETER;
}

jlong ReplyUserdataBits(std::uint64_t value) {
    std::int64_t signed_bits = 0;
    static_assert(sizeof(signed_bits) == sizeof(value), "Unexpected integer widths.");
    std::memcpy(&signed_bits, &value, sizeof(value));
    return static_cast<jlong>(signed_bits);
}

bool EventCarriesReplyUserdata(mpv_event_id event_id) {
    switch (event_id) {
        case MPV_EVENT_GET_PROPERTY_REPLY:
        case MPV_EVENT_SET_PROPERTY_REPLY:
        case MPV_EVENT_COMMAND_REPLY:
        case MPV_EVENT_PROPERTY_CHANGE:
            return true;
        default:
            return false;
    }
}

jint NativeWaitEvent(
        JNIEnv* env,
        jobject,
        jlong token,
        jdouble timeout_seconds,
        jintArray integer_output,
        jlongArray long_output,
        jdoubleArray double_output,
        jobjectArray string_output) {
    if (!EnsureArrayLength(
                env, integer_output, kIntegerFieldCount, "The event integer buffer is malformed.") ||
        !EnsureArrayLength(
                env, long_output, kLongFieldCount, "The event long buffer is malformed.") ||
        !EnsureArrayLength(
                env, double_output, kDoubleFieldCount, "The event double buffer is malformed.") ||
        !EnsureArrayLength(
                env, string_output, kStringFieldCount, "The event string buffer is malformed.")) {
        return MPV_ERROR_INVALID_PARAMETER;
    }

    ClientCall call(token, false, true);
    if (!call.valid()) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    mpv_event* event = mpv_wait_event(call.handle(), timeout_seconds);
    if (event == nullptr) {
        ThrowIllegalState(env, "mpv_wait_event returned a null event pointer.");
        return MPV_ERROR_GENERIC;
    }

    jint integers[kIntegerFieldCount] = {};
    jlong longs[kLongFieldCount] = {};
    jdouble doubles[kDoubleFieldCount] = {};
    integers[kEventId] = static_cast<jint>(event->event_id);
    integers[kEventError] = static_cast<jint>(event->error);
    if (EventCarriesReplyUserdata(event->event_id)) {
        integers[kPresenceFlags] |= kHasReplyUserdata;
        longs[kReplyUserdata] = ReplyUserdataBits(event->reply_userdata);
    }

    jstring property_name = nullptr;
    jstring property_string = nullptr;
    // Version 1 exposes synchronous getters only. Async GET_PROPERTY_REPLY
    // payloads remain outside this wire until the Kotlin API can correlate them.
    if (event->event_id == MPV_EVENT_PROPERTY_CHANGE && event->data != nullptr) {
        const auto* property = static_cast<const mpv_event_property*>(event->data);
        integers[kPropertyFormat] = static_cast<jint>(property->format);
        property_name = Utf8ToJString(env, property->name);
        if (env->ExceptionCheck()) {
            return MPV_ERROR_GENERIC;
        }
        if (property->data != nullptr) {
            switch (property->format) {
                case MPV_FORMAT_STRING:
                case MPV_FORMAT_OSD_STRING: {
                    const auto value = *static_cast<char* const*>(property->data);
                    if (value != nullptr) {
                        property_string = Utf8ToJString(env, value);
                        if (env->ExceptionCheck()) {
                            if (property_name != nullptr) {
                                env->DeleteLocalRef(property_name);
                            }
                            return MPV_ERROR_GENERIC;
                        }
                        integers[kPresenceFlags] |= kHasPropertyValue;
                    }
                    break;
                }
                case MPV_FORMAT_FLAG:
                    integers[kPropertyFlag] =
                            *static_cast<const int*>(property->data) == 0 ? 0 : 1;
                    integers[kPresenceFlags] |= kHasPropertyValue;
                    break;
                case MPV_FORMAT_INT64:
                    longs[kPropertyInt64] = static_cast<jlong>(
                            *static_cast<const std::int64_t*>(property->data));
                    integers[kPresenceFlags] |= kHasPropertyValue;
                    break;
                case MPV_FORMAT_DOUBLE:
                    doubles[kPropertyDouble] =
                            *static_cast<const double*>(property->data);
                    integers[kPresenceFlags] |= kHasPropertyValue;
                    break;
                case MPV_FORMAT_NONE:
                case MPV_FORMAT_NODE:
                case MPV_FORMAT_NODE_ARRAY:
                case MPV_FORMAT_NODE_MAP:
                case MPV_FORMAT_BYTE_ARRAY:
                    break;
            }
        }
    } else if (event->event_id == MPV_EVENT_START_FILE && event->data != nullptr) {
        const auto* start = static_cast<const mpv_event_start_file*>(event->data);
        integers[kPresenceFlags] |= kHasPlaylistEntryId;
        longs[kPlaylistEntryId] = static_cast<jlong>(start->playlist_entry_id);
    } else if (event->event_id == MPV_EVENT_END_FILE && event->data != nullptr) {
        const auto* end = static_cast<const mpv_event_end_file*>(event->data);
        integers[kPresenceFlags] |= kHasPlaylistEntryId | kHasEndReason;
        integers[kEndReason] = static_cast<jint>(end->reason);
        integers[kEndError] = static_cast<jint>(end->error);
        integers[kInsertCount] = static_cast<jint>(end->playlist_insert_num_entries);
        longs[kPlaylistEntryId] = static_cast<jlong>(end->playlist_entry_id);
        if (end->playlist_insert_id != 0) {
            integers[kPresenceFlags] |= kHasPlaylistInsertId;
            longs[kPlaylistInsertId] = static_cast<jlong>(end->playlist_insert_id);
        }
    }

    env->SetIntArrayRegion(integer_output, 0, kIntegerFieldCount, integers);
    if (!env->ExceptionCheck()) {
        env->SetLongArrayRegion(long_output, 0, kLongFieldCount, longs);
    }
    if (!env->ExceptionCheck()) {
        env->SetDoubleArrayRegion(double_output, 0, kDoubleFieldCount, doubles);
    }
    if (!env->ExceptionCheck()) {
        env->SetObjectArrayElement(string_output, kPropertyName, property_name);
    }
    if (!env->ExceptionCheck()) {
        env->SetObjectArrayElement(string_output, kPropertyString, property_string);
    }
    if (property_name != nullptr) {
        env->DeleteLocalRef(property_name);
    }
    if (property_string != nullptr) {
        env->DeleteLocalRef(property_string);
    }
    return env->ExceptionCheck() ? MPV_ERROR_GENERIC : 0;
}

jint NativeWakeup(JNIEnv*, jobject, jlong token) {
    ClientCall call(token, true);
    if (!call.valid()) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    mpv_wakeup(call.handle());
    return 0;
}

jint SetWid(mpv_handle* handle, jobject surface) {
    std::int64_t wid = surface == nullptr
            ? 0
            : static_cast<std::int64_t>(reinterpret_cast<std::intptr_t>(surface));
    return mpv_set_option(handle, kWidOption, MPV_FORMAT_INT64, &wid);
}

void FinishDestroyAttempt(
        const std::shared_ptr<Client>& client,
        bool terminal) noexcept {
    try {
        std::lock_guard<std::mutex> lock(client->mutex);
        client->destroyed = terminal;
        client->destroy_in_progress = false;
        client->idle.notify_all();
    } catch (...) {
        // JNI entry guards cannot recover a failed native mutex. Never let the
        // cleanup path throw a second exception while unwinding.
    }
}

void EraseClientToken(jlong token, const std::shared_ptr<Client>& client) {
    std::lock_guard<std::mutex> lock(g_registry_mutex);
    const auto found = g_registry.find(token);
    if (found != g_registry.end() && found->second == client) {
        g_registry.erase(found);
    }
}

jint NativeAttachSurface(JNIEnv* env, jobject, jlong token, jobject surface) {
    if (surface == nullptr) {
        ThrowIllegalArgument(env, "The Android Surface was null.");
        return MPV_ERROR_INVALID_PARAMETER;
    }
    ClientCall call(token, false);
    if (!call.valid()) {
        return MPV_ERROR_INVALID_PARAMETER;
    }
    jobject replacement = env->NewGlobalRef(surface);
    if (replacement == nullptr) {
        return MPV_ERROR_NOMEM;
    }

    std::lock_guard<std::mutex> lock(call.client()->surface_mutex);
    try {
        call.client()->retained_surfaces.push_back(replacement);
    } catch (...) {
        env->DeleteGlobalRef(replacement);
        throw;
    }
    const int status = SetWid(call.handle(), replacement);
    if (status < 0) {
        call.client()->retained_surfaces.pop_back();
        env->DeleteGlobalRef(replacement);
        return status;
    }
    // mpv applies UPDATE_VO asynchronously. Every Surface which reached wid is
    // retained until mpv_terminate_destroy confirms that no VO thread can use it.
    call.client()->current_surface = replacement;
    return status;
}

jint NativeDetachSurface(JNIEnv*, jobject, jlong token) {
    ClientCall call(token, false);
    if (!call.valid()) {
        return MPV_ERROR_INVALID_PARAMETER;
    }

    std::lock_guard<std::mutex> lock(call.client()->surface_mutex);
    if (call.client()->current_surface == nullptr) {
        return 0;
    }
    const int status = SetWid(call.handle(), nullptr);
    if (status >= 0) {
        call.client()->current_surface = nullptr;
    }
    return status;
}

jint NativeTerminateDestroy(JNIEnv* env, jobject, jlong token) {
    const std::shared_ptr<Client> client = LookupClient(token);
    if (client == nullptr) {
        return 0;
    }

    mpv_handle* handle = nullptr;
    {
        std::unique_lock<std::mutex> lock(client->mutex);
        if (client->destroyed) {
            return 0;
        }
        if (client->waiting && client->wait_thread == std::this_thread::get_id()) {
            ThrowIllegalState(env, "The native event thread cannot destroy its own client.");
            return MPV_ERROR_INVALID_PARAMETER;
        }
        while (client->destroy_in_progress) {
            client->idle.wait(lock, [&client] { return !client->destroy_in_progress; });
            if (client->destroyed) {
                return 0;
            }
        }
        client->closing = true;
        client->destroy_in_progress = true;
        handle = client->handle;
    }

    bool termination_committed = false;
    try {
        if (handle != nullptr) {
            mpv_wakeup(handle);
        }
        {
            std::unique_lock<std::mutex> lock(client->mutex);
            client->idle.wait(lock, [&client] { return client->active_calls == 0; });
            handle = client->handle;
        }

        if (handle != nullptr) {
            if (client->current_surface != nullptr) {
                const int surface_status = SetWid(handle, nullptr);
                if (surface_status < 0) {
                    FinishDestroyAttempt(client, false);
                    return static_cast<jint>(surface_status);
                }
            }
            {
                std::lock_guard<std::mutex> lock(client->mutex);
                client->handle = nullptr;
                termination_committed = true;
            }
            mpv_terminate_destroy(handle);
        }
        client->current_surface = nullptr;
        for (jobject retained_surface : client->retained_surfaces) {
            env->DeleteGlobalRef(retained_surface);
        }
        client->retained_surfaces.clear();

        FinishDestroyAttempt(client, true);
        EraseClientToken(token, client);
        return 0;
    } catch (...) {
        FinishDestroyAttempt(client, termination_committed);
        throw;
    }
}

jlong GuardedNativeCreate(
        JNIEnv* env,
        jobject receiver,
        jobject application_context) noexcept {
    return GuardToken(env, [&] { return NativeCreate(env, receiver, application_context); });
}

jint GuardedNativeSetOptionString(
        JNIEnv* env,
        jobject receiver,
        jlong token,
        jstring name,
        jstring value) noexcept {
    return GuardStatus(
            env,
            [&] { return NativeSetOptionString(env, receiver, token, name, value); });
}

jint GuardedNativeInitialize(JNIEnv* env, jobject receiver, jlong token) noexcept {
    return GuardStatus(env, [&] { return NativeInitialize(env, receiver, token); });
}

jint GuardedNativeCommand(
        JNIEnv* env,
        jobject receiver,
        jlong token,
        jobjectArray arguments) noexcept {
    return GuardStatus(env, [&] { return NativeCommand(env, receiver, token, arguments); });
}

jint GuardedNativeGetPropertyDouble(
        JNIEnv* env,
        jobject receiver,
        jlong token,
        jstring name,
        jdoubleArray output) noexcept {
    return GuardStatus(
            env,
            [&] { return NativeGetPropertyDouble(env, receiver, token, name, output); });
}

jint GuardedNativeGetPropertyBoolean(
        JNIEnv* env,
        jobject receiver,
        jlong token,
        jstring name,
        jintArray output) noexcept {
    return GuardStatus(
            env,
            [&] { return NativeGetPropertyBoolean(env, receiver, token, name, output); });
}

jint GuardedNativeSetPropertyDouble(
        JNIEnv* env,
        jobject receiver,
        jlong token,
        jstring name,
        jdouble value) noexcept {
    return GuardStatus(
            env,
            [&] { return NativeSetPropertyDouble(env, receiver, token, name, value); });
}

jint GuardedNativeSetPropertyBoolean(
        JNIEnv* env,
        jobject receiver,
        jlong token,
        jstring name,
        jboolean value) noexcept {
    return GuardStatus(
            env,
            [&] { return NativeSetPropertyBoolean(env, receiver, token, name, value); });
}

jint GuardedNativeSetPropertyString(
        JNIEnv* env,
        jobject receiver,
        jlong token,
        jstring name,
        jstring value) noexcept {
    return GuardStatus(
            env,
            [&] { return NativeSetPropertyString(env, receiver, token, name, value); });
}

jint GuardedNativeObserveProperty(
        JNIEnv* env,
        jobject receiver,
        jlong token,
        jstring name,
        jint raw_format,
        jlong reply_userdata) noexcept {
    return GuardStatus(env, [&] {
        return NativeObserveProperty(
                env, receiver, token, name, raw_format, reply_userdata);
    });
}

jint GuardedNativeUnobserveProperty(
        JNIEnv* env,
        jobject receiver,
        jlong token,
        jlong reply_userdata) noexcept {
    return GuardStatus(
            env,
            [&] { return NativeUnobserveProperty(env, receiver, token, reply_userdata); });
}

jint GuardedNativeWaitEvent(
        JNIEnv* env,
        jobject receiver,
        jlong token,
        jdouble timeout_seconds,
        jintArray integer_output,
        jlongArray long_output,
        jdoubleArray double_output,
        jobjectArray string_output) noexcept {
    return GuardStatus(env, [&] {
        return NativeWaitEvent(
                env,
                receiver,
                token,
                timeout_seconds,
                integer_output,
                long_output,
                double_output,
                string_output);
    });
}

jint GuardedNativeWakeup(JNIEnv* env, jobject receiver, jlong token) noexcept {
    return GuardStatus(env, [&] { return NativeWakeup(env, receiver, token); });
}

jint GuardedNativeAttachSurface(
        JNIEnv* env,
        jobject receiver,
        jlong token,
        jobject surface) noexcept {
    return GuardStatus(
            env,
            [&] { return NativeAttachSurface(env, receiver, token, surface); });
}

jint GuardedNativeDetachSurface(JNIEnv* env, jobject receiver, jlong token) noexcept {
    return GuardStatus(env, [&] { return NativeDetachSurface(env, receiver, token); });
}

jint GuardedNativeTerminateDestroy(JNIEnv* env, jobject receiver, jlong token) noexcept {
    return GuardStatus(env, [&] { return NativeTerminateDestroy(env, receiver, token); });
}

const JNINativeMethod kMethods[] = {
        {const_cast<char*>("nativeCreate"),
         const_cast<char*>("(Landroid/content/Context;)J"),
         reinterpret_cast<void*>(GuardedNativeCreate)},
        {const_cast<char*>("nativeSetOptionString"),
         const_cast<char*>("(JLjava/lang/String;Ljava/lang/String;)I"),
         reinterpret_cast<void*>(GuardedNativeSetOptionString)},
        {const_cast<char*>("nativeInitialize"),
         const_cast<char*>("(J)I"),
         reinterpret_cast<void*>(GuardedNativeInitialize)},
        {const_cast<char*>("nativeCommand"),
         const_cast<char*>("(J[Ljava/lang/String;)I"),
         reinterpret_cast<void*>(GuardedNativeCommand)},
        {const_cast<char*>("nativeGetPropertyDouble"),
         const_cast<char*>("(JLjava/lang/String;[D)I"),
         reinterpret_cast<void*>(GuardedNativeGetPropertyDouble)},
        {const_cast<char*>("nativeGetPropertyBoolean"),
         const_cast<char*>("(JLjava/lang/String;[I)I"),
         reinterpret_cast<void*>(GuardedNativeGetPropertyBoolean)},
        {const_cast<char*>("nativeSetPropertyDouble"),
         const_cast<char*>("(JLjava/lang/String;D)I"),
         reinterpret_cast<void*>(GuardedNativeSetPropertyDouble)},
        {const_cast<char*>("nativeSetPropertyBoolean"),
         const_cast<char*>("(JLjava/lang/String;Z)I"),
         reinterpret_cast<void*>(GuardedNativeSetPropertyBoolean)},
        {const_cast<char*>("nativeSetPropertyString"),
         const_cast<char*>("(JLjava/lang/String;Ljava/lang/String;)I"),
         reinterpret_cast<void*>(GuardedNativeSetPropertyString)},
        {const_cast<char*>("nativeObserveProperty"),
         const_cast<char*>("(JLjava/lang/String;IJ)I"),
         reinterpret_cast<void*>(GuardedNativeObserveProperty)},
        {const_cast<char*>("nativeUnobserveProperty"),
         const_cast<char*>("(JJ)I"),
         reinterpret_cast<void*>(GuardedNativeUnobserveProperty)},
        {const_cast<char*>("nativeWaitEvent"),
         const_cast<char*>("(JD[I[J[D[Ljava/lang/String;)I"),
         reinterpret_cast<void*>(GuardedNativeWaitEvent)},
        {const_cast<char*>("nativeWakeup"),
         const_cast<char*>("(J)I"),
         reinterpret_cast<void*>(GuardedNativeWakeup)},
        {const_cast<char*>("nativeAttachSurface"),
         const_cast<char*>("(JLandroid/view/Surface;)I"),
         reinterpret_cast<void*>(GuardedNativeAttachSurface)},
        {const_cast<char*>("nativeDetachSurface"),
         const_cast<char*>("(J)I"),
         reinterpret_cast<void*>(GuardedNativeDetachSurface)},
        {const_cast<char*>("nativeTerminateDestroy"),
         const_cast<char*>("(J)I"),
         reinterpret_cast<void*>(GuardedNativeTerminateDestroy)},
};

}  // namespace

extern "C" JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM* vm, void*) {
    JNIEnv* env = nullptr;
    if (vm == nullptr ||
        vm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6) != JNI_OK ||
        env == nullptr) {
        return JNI_ERR;
    }
    if (av_jni_set_java_vm(vm, nullptr) < 0) {
        return JNI_ERR;
    }

    jclass bindings = env->FindClass(kBindingsClass);
    if (bindings == nullptr) {
        return JNI_ERR;
    }
    const jint status = env->RegisterNatives(
            bindings,
            kMethods,
            static_cast<jint>(sizeof(kMethods) / sizeof(kMethods[0])));
    env->DeleteLocalRef(bindings);
    if (status != JNI_OK || env->ExceptionCheck()) {
        return JNI_ERR;
    }
    return JNI_VERSION_1_6;
}

extern "C" JNIEXPORT void JNICALL JNI_OnUnload(JavaVM*, void*) {
    // FFmpeg stores the application-context jobject as a non-owning process
    // pointer and exposes no synchronized clear operation. Deleting the one
    // global reference here would leave FFmpeg with a stale jobject; retain it
    // deliberately until process exit.
}
