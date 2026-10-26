// SPDX-License-Identifier: GPL-3.0-or-later

import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.android.library)
}

val nativeStaging = rootProject.layout.projectDirectory.dir("native/out/android")
val verifyNativeStaging = tasks.register<Exec>("verifyNativeStaging") {
    group = "verification"
    description = "Verify the accepted source-built JNI receipt and all twenty native libraries."
    val python = providers.gradleProperty("nativePython").orElse(
        if (providers.systemProperty("os.name").get().startsWith("Windows")) "python" else "python3",
    )
    workingDir(rootProject.layout.projectDirectory)
    commandLine(python.get(), "-B", "native/tools/android_staging_tool.py", "verify")
    inputs.file(rootProject.layout.projectDirectory.file("native/android-staging-lock.json"))
    inputs.dir(nativeStaging)
    // No outputs: hash verification is deliberately repeated before each build.
}

android {
    namespace = "io.github.joyelliot.zivplayer.platform.libmpv"
    compileSdk {
        version = release(37)
    }
    buildToolsVersion = "37.0.0"
    ndkVersion = "26.1.10909125"
    externalNativeBuild {
        cmake {
            path = rootProject.file("native/libvlc4-seek/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    defaultConfig {
        minSdk = 26
        consumerProguardFiles("consumer-rules.pro")

        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    packaging.jniLibs {
        // Preserve the exact bytes that passed the native audit in inspection APKs.
        keepDebugSymbols += "**/*.so"
        useLegacyPackaging = false
    }
}

androidComponents.onVariants { variant ->
    variant.sources.jniLibs?.addStaticSourceDirectory(nativeStaging.dir("jniLibs").asFile.absolutePath)
}

tasks.named("preBuild").configure { dependsOn(verifyNativeStaging) }

kotlin {
    jvmToolchain(21)
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    constraints {
        // Optional playback adapters must use the app's AndroidX Core baseline.
        implementation(libs.androidx.core.base)
        implementation(libs.androidx.core.ktx)
    }
    api(project(":core:player-runtime"))

    implementation(libs.kotlinx.coroutines.core)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.androidx.media3.exoplayer)
    implementation(libs.androidx.media3.ui)
    implementation(libs.androidx.media3.effect)
    implementation(libs.libass.media)
    implementation(libs.libass.kt)
    implementation(libs.libvlc.all)

    testImplementation(libs.junit4)
}
