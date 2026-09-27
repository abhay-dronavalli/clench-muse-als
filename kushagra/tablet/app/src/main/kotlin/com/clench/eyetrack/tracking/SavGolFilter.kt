package com.clench.eyetrack.tracking

/**
 * Savitzky-Golay smoothing filter.
 *
 * Fits a polynomial of [polyOrder] over a sliding window of [windowSize] samples
 * and returns the fitted center value. Kills high-frequency noise without the
 * phase lag of a moving average or EMA.
 *
 * Ported from scipy.signal.savgol_filter; same approach used in the DreamTeam
 * TrainOfFour project for bounding-box smoothing.
 *
 * @param windowSize Must be odd and > polyOrder.
 * @param polyOrder  Polynomial degree (3 = cubic is a good default).
 */
class SavGolFilter(
    private val windowSize: Int = 15,
    polyOrder: Int = 3,
) {
    private val coeffs: DoubleArray = computeCoeffs(windowSize, polyOrder)
    private val buffer: ArrayDeque<Double> = ArrayDeque(windowSize)

    fun push(value: Float) {
        buffer.addLast(value.toDouble())
        if (buffer.size > windowSize) buffer.removeFirst()
    }

    fun get(): Float {
        if (buffer.isEmpty()) return 0f
        if (buffer.size < windowSize) {
            // Not enough data yet — simple average as fallback
            return (buffer.sum() / buffer.size).toFloat()
        }
        var sum = 0.0
        buffer.forEachIndexed { i, v -> sum += coeffs[i] * v }
        return sum.toFloat()
    }

    fun reset() = buffer.clear()

    companion object {
        /**
         * Precompute the convolution weights for the center output point.
         *
         * Math: build Vandermonde matrix J, then coeffs = row 0 of (J^T J)^-1 J^T.
         */
        internal fun computeCoeffs(windowSize: Int, polyOrder: Int): DoubleArray {
            val half = (windowSize - 1) / 2
            val cols = polyOrder + 1

            // Vandermonde-like matrix J[i][k] = (i - half)^k
            val j = Array(windowSize) { i ->
                DoubleArray(cols) { k -> Math.pow((i - half).toDouble(), k.toDouble()) }
            }

            // J^T * J
            val jtj = Array(cols) { a ->
                DoubleArray(cols) { b ->
                    var s = 0.0
                    for (i in 0 until windowSize) s += j[i][a] * j[i][b]
                    s
                }
            }

            // Invert via Gauss-Jordan
            val aug = Array(cols) { row ->
                DoubleArray(cols * 2).also { r ->
                    for (c in 0 until cols) r[c] = jtj[row][c]
                    r[cols + row] = 1.0
                }
            }
            for (i in 0 until cols) {
                // Partial pivot
                var maxRow = i
                for (k in i + 1 until cols) {
                    if (Math.abs(aug[k][i]) > Math.abs(aug[maxRow][i])) maxRow = k
                }
                val tmp = aug[i]; aug[i] = aug[maxRow]; aug[maxRow] = tmp

                val pivot = aug[i][i]
                for (c in 0 until cols * 2) aug[i][c] /= pivot
                for (k in 0 until cols) {
                    if (k == i) continue
                    val factor = aug[k][i]
                    for (c in 0 until cols * 2) aug[k][c] -= factor * aug[i][c]
                }
            }
            val inv = Array(cols) { row -> DoubleArray(cols) { c -> aug[row][cols + c] } }

            // weights[i] = sum_k inv[0][k] * J[i][k]
            return DoubleArray(windowSize) { i ->
                var s = 0.0
                for (k in 0 until cols) s += inv[0][k] * j[i][k]
                s
            }
        }
    }
}
