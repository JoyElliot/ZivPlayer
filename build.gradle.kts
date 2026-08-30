// SPDX-License-Identifier: GPL-3.0-or-later

buildscript {
    repositories {
        google()
        mavenCentral()
    }

    dependencies {
        // AGP 9.3 embeds an older Kotlin compiler. MIUIX 0.9.3 publishes
        // Kotlin 2.4 metadata, so the project deliberately overrides it.
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
