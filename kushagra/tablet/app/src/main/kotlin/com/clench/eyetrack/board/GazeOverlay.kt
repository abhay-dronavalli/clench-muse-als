package com.clench.eyetrack.board

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/**
 * A native layer over the board's WebView for the tracker's own screens: the calibration targets, the
 * one-target check of a saved calibration, and prompts ("Calibrate now?"). Hidden otherwise, and then
 * it lets every touch through to the page.
 *
 * Styled like the onboarding on the page (web/src/board/Onboarding.tsx): a light, calm ride look with
 * a teal accent. Targets are big and high-contrast (a teal disc with a white centre to look at, a grey
 * track and a teal ring that fills as the tracker collects), and never drawn off the screen's edge.
 */
@SuppressLint("ViewConstructor")
class GazeOverlay(context: Context) : FrameLayout(context) {

    private val dot = DotView(context, ::dpf)
    private val panel = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER
        setPadding(dp(40), dp(28), dp(40), dp(28))
        background = GradientDrawable().apply {
            setColor(Color.WHITE)
            cornerRadius = dpf(28)
        }
        elevation = dpf(12)
    }
    private val step = TextView(context).apply {
        setTextColor(TEAL_DARK)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 18f)
        typeface = Typeface.DEFAULT_BOLD
        gravity = Gravity.CENTER
        letterSpacing = 0.08f
    }
    private val message = TextView(context).apply {
        setTextColor(INK)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 30f)
        typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
        gravity = Gravity.CENTER
    }
    private val buttons = LinearLayout(context).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER
        setPadding(0, 0, 0, dp(6)) // room for the buttons' rounded bottoms
        clipToPadding = false
        clipChildren = false
    }

    init {
        setBackgroundColor(BACKDROP)
        addView(dot, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
        panel.addView(step)
        panel.addView(message)
        panel.addView(buttons, LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT))
        panel.clipChildren = false
        addView(panel, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT, Gravity.CENTER).apply {
            setMargins(dp(24), dp(24), dp(24), dp(24))
        })
        visibility = GONE
    }

    /** Where this layer sits on the screen, in pixels (the SDK's calibration points are screen pixels). */
    fun screenRect(): GazeMath.ViewRect {
        val loc = IntArray(2)
        getLocationOnScreen(loc)
        return GazeMath.ViewRect(loc[0].toFloat(), loc[1].toFloat(), width.toFloat(), height.toFloat())
    }

    /** The target's reach from its centre (halo included), in pixels: calibration keeps points this far in. */
    fun targetReach(): Float = dpf(DotView.HALO_DP) + dpf(8)

    /** A target at screen point (x, y); `progress` 0..1 fills its ring. A card of text at the top, optional. */
    fun showDot(x: Float, y: Float, progress: Float, text: String = "", stepText: String = "") {
        val r = screenRect()
        visibility = VISIBLE
        panel.visibility = if (text.isEmpty()) GONE else VISIBLE
        step.visibility = if (stepText.isEmpty()) GONE else VISIBLE
        step.text = stepText.uppercase()
        message.text = text
        buttons.removeAllViews()
        (panel.layoutParams as LayoutParams).gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
        dot.set(x - r.left, y - r.top, progress)
    }

    /** A question with buttons; the board stays covered until one is chosen. The first is the main one. */
    fun prompt(text: String, vararg choices: Pair<String, () -> Unit>) {
        visibility = VISIBLE
        dot.clear()
        panel.visibility = VISIBLE
        step.visibility = GONE
        (panel.layoutParams as LayoutParams).gravity = Gravity.CENTER
        message.text = text
        buttons.removeAllViews()
        choices.forEachIndexed { i, (label, action) ->
            buttons.addView(
                TextView(context).apply {
                    this.text = label
                    setTextSize(TypedValue.COMPLEX_UNIT_SP, 24f)
                    typeface = Typeface.DEFAULT_BOLD
                    gravity = Gravity.CENTER
                    setPadding(dp(28), dp(16), dp(28), dp(16))
                    setTextColor(if (i == 0) Color.WHITE else INK)
                    background = GradientDrawable().apply {
                        cornerRadius = dpf(20)
                        if (i == 0) setColor(TEAL) else {
                            setColor(Color.WHITE)
                            setStroke(dp(2), Color.rgb(212, 220, 218))
                        }
                    }
                    isClickable = true
                    setOnClickListener { action() }
                },
                LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                    setMargins(dp(12), dp(28), dp(12), 0)
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

    private fun dp(v: Int) = dpf(v).toInt()
    private fun dpf(v: Int) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics)

    private class DotView(context: Context, private val dpf: (Int) -> Float) : View(context) {
        private var x = -1f
        private var y = -1f
        private var progress = 0f
        private val halo = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.argb(46, 0, 169, 157) }
        private val track = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.rgb(214, 224, 222)
            style = Paint.Style.STROKE
            strokeWidth = dpf(8)
        }
        private val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = TEAL
            style = Paint.Style.STROKE
            strokeWidth = dpf(8)
            strokeCap = Paint.Cap.ROUND
        }
        private val core = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = TEAL }
        private val centre = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE }
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
            val ringR = dpf(RING_DP)
            canvas.drawCircle(x, y, dpf(HALO_DP), halo)
            canvas.drawCircle(x, y, ringR, track)
            if (progress > 0f) {
                arc.set(x - ringR, y - ringR, x + ringR, y + ringR)
                canvas.drawArc(arc, -90f, 360f * progress, false, ring)
            }
            canvas.drawCircle(x, y, dpf(22), core)
            canvas.drawCircle(x, y, dpf(7), centre) // the exact spot to look at
        }

        companion object {
            const val HALO_DP = 64
            const val RING_DP = 44
        }
    }

    companion object {
        val TEAL = Color.rgb(0, 169, 157)
        val TEAL_DARK = Color.rgb(0, 122, 114)
        val INK = Color.rgb(20, 24, 27)
        val BACKDROP = Color.rgb(242, 246, 245)
    }
}
