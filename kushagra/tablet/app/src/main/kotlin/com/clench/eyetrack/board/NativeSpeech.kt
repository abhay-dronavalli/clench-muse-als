package com.clench.eyetrack.board

import android.content.Context
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import java.util.Locale

/**
 * Android's text-to-speech for the board: the WebView has no speechSynthesis, so without this the
 * tablet says nothing whenever the board falls back to "browser speech" (no ElevenLabs key, an
 * uncached picked word, ElevenLabs slow or failing). Page side: web/src/board/nativeSpeech.ts.
 *
 * One line at a time: the page's sound queue orders them, and each new line replaces the last.
 * Every accepted line ends with exactly one [onEnd] ("done" or "error") with its id; a line cut off by
 * the next one or by stop() also ends "done" (the page has already moved on and ignores it).
 */
class NativeSpeech(context: Context, private val onEnd: (id: String, state: String, detail: String) -> Unit) {
    @Volatile private var ready = false
    private val tts: TextToSpeech = TextToSpeech(context.applicationContext) { status ->
        ready = status == TextToSpeech.SUCCESS
        Log.i(TAG, if (ready) "Android voice ready" else "Android voice failed to start ($status)")
    }

    init {
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(id: String) {}
            override fun onDone(id: String) = onEnd(id, "done", "")
            override fun onStop(id: String, interrupted: Boolean) = onEnd(id, "done", "stopped")
            @Deprecated("the platform's older overload")
            override fun onError(id: String) = onEnd(id, "error", "")
            override fun onError(id: String, errorCode: Int) = onEnd(id, "error", "error $errorCode")
        })
    }

    /** Say [text] in [lang] (a BCP 47 tag, e.g. es-US) at [volume] 0..1. False = not ready or refused. */
    fun speak(id: String, text: String, lang: String, volume: Float): Boolean {
        if (!ready) return false
        val locale = Locale.forLanguageTag(lang)
        if (tts.voice?.locale != locale) {
            val result = tts.setLanguage(locale)
            if (result < TextToSpeech.LANG_AVAILABLE) Log.w(TAG, "no Android voice for $lang ($result); using the default")
        }
        val params = Bundle().apply { putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, volume.coerceIn(0f, 1f)) }
        return tts.speak(text, TextToSpeech.QUEUE_FLUSH, params, id) == TextToSpeech.SUCCESS
    }

    fun stop() {
        if (ready) tts.stop()
    }

    fun shutdown() {
        ready = false
        tts.shutdown()
    }

    companion object {
        private const val TAG = "NativeSpeech"
    }
}
