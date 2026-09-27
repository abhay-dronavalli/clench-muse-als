package com.clench.eyetrack.board

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/**
 * A native layer over the board's WebView for the tracker's own screens: the calibration dots, the
 * one-target check of a saved calibration, and prompts ("Calibrate now?"). Hidden otherwise, and
 * then it lets every touch through to the page.
 */
@SuppressLint("ViewConstructor")
class GazeOverlay(context: Context) : FrameLayout(context) {

    private val dot = DotView(context)
    private val panel = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER
        setPadding(dp(32), dp(24), dp(32), dp(24))
        setBackgroundColor(Color.argb(235, 24, 24, 27))
    }
    private val message = TextView(context).apply {
        setTextColor(Color.WHITE)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 28f)
        gravity = Gravity.CENTER
    }
    private val buttons = LinearLayout(context).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER
    }

    init {
        setBackgroundColor(Color.BLACK)
        addView(dot, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
        panel.addView(message)
        panel.addView(buttons)
        addView(panel, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT, Gravity.CENTER))
        visibility = GONE
    }

    /** Where this layer sits on the screen, in pixels (the SDK's calibration points are screen pixels). */
    fun screenRect(): GazeMath.ViewRect {
        val loc = IntArray(2)
        getLocationOnScreen(loc)
        return GazeMath.ViewRect(loc[0].toFloat(), loc[1].toFloat(), width.toFloat(), height.toFloat())
    }

    /** A target dot at screen point (x, y); `progress` 0..1 fills its ring. Text above it, optional. */
    fun showDot(x: Float, y: Float, progress: Float, text: String = "") {
        val r = screenRect()
        visibility = VISIBLE
        panel.visibility = if (text.isEmpty()) GONE else VISIBLE
        message.text = text
        buttons.removeAllViews()
        (panel.layoutParams as LayoutParams).gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
        dot.set(x - r.left, y - r.top, progress)
    }

    /** A question with buttons; the board stays covered until one is chosen. */
    fun prompt(text: String, vararg choices: Pair<String, () -> Unit>) {
        visibility = VISIBLE
        dot.clear()
        panel.visibility = VISIBLE
        (panel.layoutParams as LayoutParams).gravity = Gravity.CENTER
        message.text = text
        buttons.removeAllViews()
        for ((label, action) in choices) {
            buttons.addView(
                Button(context).apply {
                    this.text = label
                    setTextSize(TypedValue.COMPLEX_UNIT_SP, 24f)
                    setOnClickListener { action() }
                },
                LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                    setMargins(dp(16), dp(24), dp(16), 0)
                },
            )
        }
        requestLayout()
    }

    fun hide() {
        visibility = GONE
        dot.clear()
        buttons.removeAllViews()
    }

    private fun dp(v: Int) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics).toInt()

    private class DotView(context: Context) : View(context) {
        private var x = -1f
        private var y = -1f
        private var progress = 0f
        private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.rgb(239, 68, 68) }
        private val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.rgb(253, 224, 71)
            style = Paint.Style.STROKE
            strokeWidth = 10f
            strokeCap = Paint.Cap.ROUND
        }
        private val arc = RectF()

        fun set(x: Float, y: Float, progress: Float) {
            this.x = x
            this.y = y
            this.progress = progress
            invalidate()
        }

        fun clear() = set(-1f, -1f, 0f)

        override fun onDraw(canvas: Canvas) {
            if (x < 0) return
            canvas.drawCircle(x, y, 22f, fill)
            if (progress > 0f) {
                arc.set(x - 44f, y - 44f, x + 44f, y + 44f)
                canvas.drawArc(arc, -90f, 360f * progress, false, ring)
            }
        }
    }
}
