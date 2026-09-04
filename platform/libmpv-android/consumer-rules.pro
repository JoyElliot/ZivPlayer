# libzivplayer_mpv resolves this class and its private native methods from
# JNI_OnLoad/RegisterNatives. Keep the binary ABI stable through app shrinking.
-keep class io.github.joyelliot.zivplayer.platform.libmpv.MpvNativeBindings {
    native <methods>;
}
