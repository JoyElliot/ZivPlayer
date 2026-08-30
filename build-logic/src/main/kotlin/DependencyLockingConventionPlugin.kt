// SPDX-License-Identifier: GPL-3.0-or-later

import org.gradle.api.Plugin
import org.gradle.api.Project
import org.gradle.api.artifacts.result.UnresolvedDependencyResult

class DependencyLockingConventionPlugin : Plugin<Project> {
    override fun apply(target: Project) {
        target.allprojects {
            dependencyLocking {
                lockAllConfigurations()
            }

            tasks.register("resolveAndLockAll") {
                group = "dependency management"
                description = "Resolves every consumer dependency graph and writes dependency locks."
                notCompatibleWithConfigurationCache(
                    "This task deliberately resolves every consumer dependency graph.",
                )

                doFirst {
                    require(gradle.startParameter.isWriteDependencyLocks) {
                        "Run this task with --write-locks."
                    }
                }

                doLast {
                    configurations
                        .filter { it.isCanBeResolved && !it.isCanBeConsumed }
                        .sortedBy { it.name }
                        .forEach { configuration ->
                            // Resolving the graph is sufficient for dependency locking. Resolving
                            // files would also select AGP artifact types and can be ambiguous for
                            // project dependencies such as Android library variants.
                            val result = configuration.incoming.resolutionResult
                            result.allComponents

                            val unresolved = result.allDependencies
                                .filterIsInstance<UnresolvedDependencyResult>()
                            check(unresolved.isEmpty()) {
                                "${configuration.name} has unresolved dependencies: " +
                                    unresolved.joinToString { dependency ->
                                        "${dependency.requested}: ${dependency.failure.message}"
                                    }
                            }
                        }
                }
            }
        }
    }
}
