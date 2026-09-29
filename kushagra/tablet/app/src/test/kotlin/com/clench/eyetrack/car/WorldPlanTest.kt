package com.clench.eyetrack.car

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

class WorldPlanTest {
    @Test
    fun nothingTallEverStandsInsideTheCameraCircle() {
        for (seed in 1L..50L) {
            val plan = WorldPlan.plan(seed)
            assertTrue("seed $seed", WorldPlan.keepsClear(plan))
            // And after every piece has been re-rolled a few times (as they wrap around).
            val rng = Random(seed * 31)
            repeat(5) { plan.forEach { WorldPlan.reroll(it, rng) } }
            assertTrue("seed $seed after re-rolls", WorldPlan.keepsClear(plan))
        }
    }

    @Test
    fun flowersAndRocksStayOffTheRoad() {
        val plan = WorldPlan.plan(7)
        plan.filter { it.kind == WorldPlan.Kind.FLOWER || it.kind == WorldPlan.Kind.ROCK }.forEach {
            assertTrue(it.z > WorldPlan.ROAD_RIGHT || it.z < WorldPlan.ROAD_LEFT)
        }
    }

    @Test
    fun eachTripLooksDifferentButASeedIsRepeatable() {
        assertEquals(WorldPlan.plan(42).map { it.z }, WorldPlan.plan(42).map { it.z })
        assertNotEquals(WorldPlan.plan(42).map { it.z }, WorldPlan.plan(43).map { it.z })
    }

    @Test
    fun theWorldIsVaried() {
        val kinds = WorldPlan.plan(3).map { it.kind }.toSet()
        assertEquals(WorldPlan.Kind.entries.toSet(), kinds)
    }

    @Test
    fun aPieceChangesLapWhenItWrapsAndFarThingsMoveSlower() {
        val tree = WorldPlan.plan(5).first { it.kind == WorldPlan.Kind.ROUND_TREE }
        val hill = WorldPlan.plan(5).first { it.kind == WorldPlan.Kind.HILL }
        assertNotEquals(WorldPlan.lap(tree, 0f), WorldPlan.lap(tree, tree.span))
        assertEquals(WorldPlan.lap(hill, 0f), WorldPlan.lap(hill, 1f)) // a hill barely moves
    }
}
