// SPDX-License-Identifier: GPL-3.0-or-later

import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins { alias(libs.plugins.android.library) }

android {
    namespace = "io.github.joyelliot.zivplayer.platform.exoplayer"
    compileSdk { version = release(37) }
    buildToolsVersion = "37.0.0"
    defaultConfig {
        minSdk = 26
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    jvmToolchain(21)
    compilerOptions { jvmTarget.set(JvmTarget.JVM_17) }
}

androidComponents.onVariants { variant ->
    variant.androidTest?.sources?.assets?.addStaticSourceDirectory(
        rootProject.layout.projectDirectory.dir("native/out/device-fixtures").asFile.absolutePath,
    )
}

dependencies {
    api(project(":core:player-runtime"))
    api(libs.androidx.media3.common)
    implementation(libs.androidx.media3.exoplayer)
    implementation(libs.kotlinx.coroutines.android)
    androidTestImplementation(libs.androidx.test.ext.junit)
    androidTestImplementation(libs.androidx.test.runner)
    testImplementation(libs.junit4)
    testImplementation(libs.kotlinx.coroutines.test)
}
