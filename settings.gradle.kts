pluginManagement {
    includeBuild("build-logic")

    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "ZivPlayer"

include(":apps:android")
include(":core:model")
include(":core:media-api")
include(":core:player-api")
include(":core:player-runtime")
include(":data:media-android")
include(":feature:player")
include(":platform:libmpv-android")
include(":platform:playback-android")
include(":ui:design-system-miuix")
