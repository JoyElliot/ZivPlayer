// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Assert.assertFalse
import org.junit.Test

class SurfaceLeaseControllerTest {
    @Test
    fun `only the current live lease may resize the native surface`() {
        val controller = controller(mutableListOf())
        val foreign = controller(mutableListOf()).attach("foreign")
        val old = controller.attach("old")
        val current = controller.attach("current")
        assertFalse(controller.owns(old))
        assertFalse(controller.owns(foreign))
        assertTrue(controller.owns(current))
        controller.detach(current)
        assertFalse(controller.owns(current))
    }

    @Test
    fun `replacement detaches the old surface before attaching the new one`() {
        val operations = mutableListOf<String>()
        val controller = controller(operations)

        val first = controller.attach("first")
        val second = controller.attach("second")

        assertNotEquals(first, second)
        assertEquals(
            listOf("attach:first", "detach", "attach:second"),
            operations,
        )
    }

    @Test
    fun `a stale lease cannot detach the replacement`() {
        val operations = mutableListOf<String>()
        val controller = controller(operations)
        val stale = controller.attach("first")
        val current = controller.attach("second")

        controller.detach(stale)
        controller.detach(current)
        controller.detach(current)

        assertEquals(
            listOf("attach:first", "detach", "attach:second", "detach"),
            operations,
        )
    }

    @Test
    fun `a lease from another backend cannot detach this backend`() {
        val firstOperations = mutableListOf<String>()
        val secondOperations = mutableListOf<String>()
        val firstController = controller(firstOperations)
        val secondController = controller(secondOperations)
        val foreignLease = firstController.attach("first")
        val localLease = secondController.attach("second")

        secondController.detach(foreignLease)
        secondController.detach(localLease)

        assertEquals(listOf("attach:first"), firstOperations)
        assertEquals(listOf("attach:second", "detach"), secondOperations)
    }

    @Test
    fun `failed detach keeps the current lease retryable`() {
        val operations = mutableListOf<String>()
        var failDetach = true
        val controller = SurfaceLeaseController<String>(
            attachNative = { operations += "attach:$it" },
            detachNative = {
                operations += "detach"
                if (failDetach) {
                    failDetach = false
                    error("detach failed")
                }
            },
        )
        val lease = controller.attach("surface")

        assertThrows(IllegalStateException::class.java) {
            controller.detach(lease)
        }
        controller.detach(lease)

        assertEquals(listOf("attach:surface", "detach", "detach"), operations)
    }

    @Test
    fun `failed close remains retryable`() {
        val operations = mutableListOf<String>()
        var failDetach = true
        val controller = SurfaceLeaseController<String>(
            attachNative = { operations += "attach:$it" },
            detachNative = {
                operations += "detach"
                if (failDetach) {
                    failDetach = false
                    error("detach failed")
                }
            },
        )
        controller.attach("surface")

        assertThrows(IllegalStateException::class.java, controller::close)
        controller.close()
        controller.close()

        assertEquals(listOf("attach:surface", "detach", "detach"), operations)
    }

    @Test
    fun `native destruction finalizes a failed close without another detach`() {
        val operations = mutableListOf<String>()
        val controller = SurfaceLeaseController<String>(
            attachNative = { operations += "attach:$it" },
            detachNative = {
                operations += "detach"
                error("detach failed")
            },
        )
        val lease = controller.attach("surface")

        assertThrows(IllegalStateException::class.java, controller::close)
        controller.completeAfterNativeDestroy()
        controller.detach(lease)
        controller.close()

        assertEquals(listOf("attach:surface", "detach"), operations)
        assertThrows(IllegalStateException::class.java) {
            controller.attach("late")
        }
    }

    @Test
    fun `close detaches once and invalidates outstanding leases`() {
        val operations = mutableListOf<String>()
        val controller = controller(operations)
        val lease = controller.attach("surface")

        controller.close()
        controller.detach(lease)
        controller.close()

        assertEquals(listOf("attach:surface", "detach"), operations)
        assertThrows(IllegalStateException::class.java) {
            controller.attach("late")
        }
    }

    @Test
    fun `failed attach attempts native cleanup and leaves no current lease`() {
        val operations = mutableListOf<String>()
        var failAttach = true
        val controller = SurfaceLeaseController<String>(
            attachNative = {
                operations += "attach:$it"
                if (failAttach) {
                    failAttach = false
                    error("attach failed")
                }
            },
            detachNative = { operations += "detach" },
        )

        assertThrows(IllegalStateException::class.java) {
            controller.attach("broken")
        }
        val lease = controller.attach("working")
        controller.detach(lease)

        assertEquals(
            listOf("attach:broken", "detach", "attach:working", "detach"),
            operations,
        )
    }

    private fun controller(operations: MutableList<String>) =
        SurfaceLeaseController<String>(
            attachNative = { operations += "attach:$it" },
            detachNative = { operations += "detach" },
        )
}
