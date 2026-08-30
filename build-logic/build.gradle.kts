// SPDX-License-Identifier: GPL-3.0-or-later

plugins {
    `kotlin-dsl`
}

group = "io.github.joyelliot.zivplayer.buildlogic"

gradlePlugin {
    plugins {
        register("dependencyLocking") {
            id = "zivplayer.dependency-locking"
            implementationClass = "DependencyLockingConventionPlugin"
        }
    }
}
