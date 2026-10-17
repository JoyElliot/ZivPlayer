// SPDX-License-Identifier: GPL-3.0-or-later

buildscript {
    repositories {
        google()
        mavenCentral()
    }

    dependencies {
        // Keep AGP's built-in Kotlin compiler aligned with the Kotlin/Compose
        // plugins and the Kotlin 2.4 metadata published by MIUIX.
        classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:${libs.versions.kotlin.get()}")
    }

    configurations.classpath {
        resolutionStrategy.activateDependencyLocking()
    }
}

plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.android.library) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.kotlin.jvm) apply false
    alias(libs.plugins.ksp) apply false
    alias(libs.plugins.room) apply false
    id("zivplayer.dependency-locking")
}
