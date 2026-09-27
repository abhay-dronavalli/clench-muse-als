package com.clench.eyetrack.muse

import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.ParcelUuid
import android.os.SystemClock
import android.util.Log
import java.util.UUID

/**
 * The Muse 2 over Bluetooth LE, straight from Android (no BrainFlow): find it, connect, subscribe to
 * TP9 / AF7 / AF8 / TP10 and the gyroscope, send the start commands, and feed [window]. A lost or
 * failed connection is retried with sensor/main.py's back-off (5, 10, 20, 40 s).
 *
 * Every Bluetooth call runs on one background thread, one GATT operation at a time. The caller must
 * hold BLUETOOTH_SCAN and BLUETOOTH_CONNECT (Android 12+) before start().
 */
@SuppressLint("MissingPermission")
class MuseBle(
    context: Context,
    /** exact Bluetooth name, e.g. "Muse-1234"; null takes the first Muse seen */
    private val name: String?,
    private val window: MuseWindow,
    private val listener: Listener,
) {
    interface Listener {
        /** Looking for the headband or connecting to it. */
        fun onConnecting(detail: String)
        /** Subscribed and started: samples are arriving in the window. */
        fun onStreaming(device: String)
        /** The connection failed or dropped; a retry is scheduled. */
        fun onLost(detail: String)
    }

    private val app = context.applicationContext
    private val adapter = (app.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager).adapter
    private val thread = HandlerThread("muse-ble").apply { start() }
    private val handler = Handler(thread.looper)

    private var running = false
    private var gatt: BluetoothGatt? = null
    private var scanning: ScanCallback? = null
    private var attempts = 0
    private var streaming = false
    private var generation = 0 // bumps on every teardown, so stale callbacks and timers do nothing
    private val ops = ArrayDeque<(BluetoothGatt) -> Boolean>()

    fun start() {
        handler.post {
            if (running) return@post
            running = true
            attempts = 0
            connect()
        }
    }

    fun stop() {
        handler.post {
            running = false
            teardown()
        }
        thread.quitSafely()
    }

    /** The stream went quiet (seen by the reader): drop the link and reconnect after 5 s. */
    fun restart(reason: String) {
        handler.post {
            if (!running) return@post
            teardown()
            attempts = 0
            listener.onLost("$reason; reconnecting in ${RETRY_MS / 1000} s")
            retry(RETRY_MS)
        }
    }

    // --- connect ---------------------------------------------------------------------------

    private fun connect() {
        if (!running) return
        if (adapter == null || !adapter.isEnabled) return fail("Bluetooth is off")
        val scanner = adapter.bluetoothLeScanner ?: return fail("Bluetooth LE scanner unavailable")
        listener.onConnecting("Looking for ${name ?: "a Muse"}")
        val gen = generation
        val cb = object : ScanCallback() {
            override fun onScanResult(callbackType: Int, result: ScanResult) {
                handler.post { if (gen == generation && scanning === this) found(result) }
            }

            override fun onScanFailed(errorCode: Int) {
                handler.post { if (gen == generation && scanning === this) fail("Bluetooth scan failed ($errorCode)") }
            }
        }
        scanning = cb
        // No hardware filter: match the name or the Muse service in the advertisement ourselves.
        scanner.startScan(null, ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(), cb)
        handler.postDelayed({
            if (gen == generation && scanning === cb) fail("No ${name ?: "Muse"} found (is it on, and not connected to the laptop?)")
        }, SCAN_TIMEOUT_MS)
    }

    private fun found(result: ScanResult) {
        val device = result.device
        val seen = result.scanRecord?.deviceName ?: device.name
        val isMuse = if (name != null) seen == name else
            seen?.startsWith("Muse") == true || result.scanRecord?.serviceUuids?.contains(ParcelUuid(MuseProtocol.SERVICE)) == true
        if (!isMuse) return
        stopScan()
        listener.onConnecting("Connecting to ${seen ?: device.address}")
        val gen = generation
        gatt = device.connectGatt(app, false, Callback(gen, seen ?: device.address), BluetoothDevice.TRANSPORT_LE)
        handler.postDelayed({ if (gen == generation && !streaming) fail("Connecting to the headband timed out") }, CONNECT_TIMEOUT_MS)
    }

    private inner class Callback(val gen: Int, val label: String) : BluetoothGattCallback() {
        private fun on(block: () -> Unit) {
            handler.post { if (gen == generation) block() }
        }

        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) = on {
            if (newState == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                g.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH)
                if (!g.discoverServices()) fail("Could not read the headband's services")
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                fail(if (streaming) "Headband disconnected" else "Connection failed (GATT status $status)")
            }
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) = on {
            val service = g.getService(MuseProtocol.SERVICE)
            if (status != BluetoothGatt.GATT_SUCCESS || service == null) return@on fail("$label is not a Muse (no Muse service)")
            val notify = (MuseProtocol.EEG + MuseProtocol.GYRO).map {
                service.getCharacteristic(it) ?: return@on fail("$label has no characteristic $it")
            }
            val control = service.getCharacteristic(MuseProtocol.CONTROL) ?: return@on fail("$label has no control characteristic")
            window.clear(SystemClock.elapsedRealtime())
            ops.clear()
            notify.forEach { ch -> ops.addLast { enableNotify(it, ch) } }
            MuseProtocol.START_COMMANDS.forEach { cmd -> ops.addLast { write(it, control, MuseProtocol.command(cmd)) } }
            next(g)
        }

        override fun onDescriptorWrite(g: BluetoothGatt, d: BluetoothGattDescriptor, status: Int) = on { done(g, status, "subscribe") }

        override fun onCharacteristicWrite(g: BluetoothGatt, c: BluetoothGattCharacteristic, status: Int) = on { done(g, status, "start command") }

        private fun done(g: BluetoothGatt, status: Int, what: String) {
            if (status != BluetoothGatt.GATT_SUCCESS) return fail("Headband refused the $what (GATT status $status)")
            if (ops.isEmpty()) {
                if (!streaming) {
                    streaming = true
                    attempts = 0
                    Log.i(TAG, "streaming from $label")
                    listener.onStreaming(label)
                }
            } else {
                next(g)
            }
        }

        // Data: straight into the window, on the binder thread (the window locks).
        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic, value: ByteArray) = data(c.uuid, value)

        @Deprecated("Android 12 and older")
        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic) {
            @Suppress("DEPRECATION")
            val value = c.value ?: return
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) data(c.uuid, value)
        }

        private fun data(uuid: UUID, value: ByteArray) {
            if (gen != generation) return
            val now = SystemClock.elapsedRealtime()
            val channel = MuseProtocol.EEG.indexOf(uuid)
            if (channel >= 0) {
                MuseProtocol.eeg(value)?.let { window.onEeg(channel, it, now) }
            } else if (uuid == MuseProtocol.GYRO) {
                MuseProtocol.gyro(value)?.let { window.onGyro(it, now) }
            }
        }
    }

    private fun next(g: BluetoothGatt) {
        val op = ops.removeFirstOrNull() ?: return
        if (!op(g)) fail("A Bluetooth request to the headband failed")
    }

    private fun enableNotify(g: BluetoothGatt, ch: BluetoothGattCharacteristic): Boolean {
        if (!g.setCharacteristicNotification(ch, true)) return false
        val d = ch.getDescriptor(MuseProtocol.CCCD) ?: return false
        val v = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            g.writeDescriptor(d, v) == BluetoothGatt.GATT_SUCCESS
        } else {
            @Suppress("DEPRECATION")
            d.value = v
            @Suppress("DEPRECATION")
            g.writeDescriptor(d)
        }
    }

    private fun write(g: BluetoothGatt, ch: BluetoothGattCharacteristic, value: ByteArray): Boolean {
        val type = if (ch.properties and BluetoothGattCharacteristic.PROPERTY_WRITE != 0) {
            BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
        } else {
            BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
        }
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            g.writeCharacteristic(ch, value, type) == BluetoothGatt.GATT_SUCCESS
        } else {
            @Suppress("DEPRECATION")
            ch.value = value
            ch.writeType = type
            @Suppress("DEPRECATION")
            g.writeCharacteristic(ch)
        }
    }

    // --- failure and retry -----------------------------------------------------------------

    private fun fail(detail: String) {
        if (!running) return
        val wasStreaming = streaming
        teardown()
        attempts = if (wasStreaming) 1 else attempts + 1
        // Back off: hammering the headband right after a dropped link keeps it busy (sensor/main.py).
        val wait = if (wasStreaming) RETRY_MS else minOf(RETRY_MS shl minOf(attempts - 1, 3), MAX_RETRY_MS)
        Log.w(TAG, "$detail; retrying in ${wait / 1000} s")
        listener.onLost("$detail; retrying in ${wait / 1000} s")
        retry(wait)
    }

    private fun retry(wait: Long) {
        val gen = generation
        handler.postDelayed({ if (gen == generation) connect() }, wait)
    }

    private fun stopScan() {
        val cb = scanning ?: return
        scanning = null
        runCatching { adapter?.bluetoothLeScanner?.stopScan(cb) }
    }

    private fun teardown() {
        generation++
        stopScan()
        ops.clear()
        streaming = false
        gatt?.let { g ->
            runCatching { g.disconnect() }
            runCatching { g.close() }
        }
        gatt = null
    }

    companion object {
        private const val TAG = "MuseBle"
        private const val RETRY_MS = 5_000L
        private const val MAX_RETRY_MS = 40_000L
        private const val SCAN_TIMEOUT_MS = 15_000L
        private const val CONNECT_TIMEOUT_MS = 15_000L
    }
}
