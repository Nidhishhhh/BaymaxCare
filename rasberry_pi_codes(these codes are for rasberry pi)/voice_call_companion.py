"""
BaymaxCare In-Home Companion - 2-Way Voice Call Client
Runs on Raspberry Pi with USB Microphone & Speaker.
Connects with Doctor and Guardian dashboards over WebRTC / WebSocket.
Supports automatic SSL/WSS self-signed negotiation and hardware ALSA rate fallback.
"""

import sys
import time
import json
import logging
import threading
import asyncio
import ssl
import array
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("baymaxcare.voice")

# Configuration
SERVER_HOST = "192.168.31.172"  # Change to your PC/Server IP or domain
SERVER_PORT = 8000
PATIENT_CODE = "P-1042"
DEVICE_IDENTITY = f"companion_{PATIENT_CODE}"

# Audio configuration
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK = 1024

try:
    import websockets
except ImportError:
    print("Please install websockets: pip install websockets")
    sys.exit(1)

try:
    import pyaudio
    AUDIO_AVAILABLE = True
except ImportError:
    logger.warning("pyaudio not installed. Audio capture/playback will be simulated.")
    AUDIO_AVAILABLE = False


def get_ssl_context():
    """Returns an SSL context that accepts self-signed certificates for local HTTPS/WSS."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    except Exception:
        return None


def resample_pcm16(raw_bytes: bytes, src_rate: int, dst_rate: int = SAMPLE_RATE) -> bytes:
    """
    Fast linear interpolation PCM 16-bit mono resampler.
    Zero external dependencies, fully compatible with Python 3.10-3.13 (no audioop needed).
    """
    if src_rate == dst_rate or not raw_bytes:
        return raw_bytes
    samples = array.array('h')
    samples.frombytes(raw_bytes)
    n_in = len(samples)
    if n_in == 0:
        return b''
    ratio = dst_rate / src_rate
    n_out = int(n_in * ratio)
    out_samples = array.array('h', [0] * n_out)
    for i in range(n_out):
        src_pos = i / ratio
        idx = int(src_pos)
        frac = src_pos - idx
        if idx + 1 < n_in:
            val = int(samples[idx] * (1.0 - frac) + samples[idx + 1] * frac)
        else:
            val = samples[idx]
        out_samples[i] = max(-32768, min(32767, val))
    return out_samples.tobytes()


class BaymaxVoiceCompanion:
    def __init__(self, host=SERVER_HOST, port=SERVER_PORT, patient_code=PATIENT_CODE, on_call_status_change=None):
        self.host = host
        self.port = port
        self.patient_code = patient_code
        self.on_call_status_change = on_call_status_change
        self.in_call = False
        self.current_caller = None
        self.ws = None
        self.audio = pyaudio.PyAudio() if AUDIO_AVAILABLE else None
        self.input_stream = None
        self.output_stream = None
        self.actual_input_rate = SAMPLE_RATE
        self.actual_output_rate = SAMPLE_RATE
        self.stop_audio = threading.Event()
        self.running = True

    def start_audio_streams(self):
        """Starts microphone recording and speaker playback streams with rate fallback."""
        if not AUDIO_AVAILABLE or not self.audio:
            logger.info("Audio hardware not available; running in test simulation mode.")
            return

        # 1. Open speaker playback stream (probe 16000, 48000, 44100)
        for rate in [SAMPLE_RATE, 48000, 44100]:
            try:
                self.output_stream = self.audio.open(
                    format=pyaudio.paInt16,
                    channels=CHANNELS,
                    rate=rate,
                    output=True,
                    frames_per_buffer=CHUNK
                )
                self.actual_output_rate = rate
                logger.info(f"Speaker stream opened at {rate} Hz.")
                break
            except Exception as e:
                logger.debug(f"Speaker rate {rate} Hz not accepted: {e}")

        # 2. Open microphone input stream (probe 16000, 48000, 44100)
        for rate in [SAMPLE_RATE, 48000, 44100]:
            try:
                self.input_stream = self.audio.open(
                    format=pyaudio.paInt16,
                    channels=CHANNELS,
                    rate=rate,
                    input=True,
                    frames_per_buffer=CHUNK
                )
                self.actual_input_rate = rate
                logger.info(f"Microphone stream opened at {rate} Hz.")
                break
            except Exception as e:
                logger.debug(f"Microphone rate {rate} Hz not accepted: {e}")

        if not self.input_stream:
            logger.warning("Could not open microphone stream on any probed sample rate.")
        if not self.output_stream:
            logger.warning("Could not open speaker stream on any probed sample rate.")

    def stop_audio_streams(self):
        """Stops and cleans up audio streams."""
        self.stop_audio.set()
        if self.input_stream:
            try:
                self.input_stream.stop_stream()
                self.input_stream.close()
            except Exception:
                pass
            self.input_stream = None

        if self.output_stream:
            try:
                self.output_stream.stop_stream()
                self.output_stream.close()
            except Exception:
                pass
            self.output_stream = None
        logger.info("Audio streams stopped.")

    def play_chime(self, ring_type="ring"):
        """Plays a friendly alert chime for incoming calls."""
        logger.info(f"*** 🔔 CHIME: {ring_type.upper()} - Incoming 2-way call from family/doctor ***")
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.say("Connecting call with your care team.")
            engine.runAndWait()
        except Exception:
            pass

    async def mic_sender(self, websocket):
        """Captures microphone and sends real-time binary PCM audio chunks."""
        logger.info("Started microphone stream sender...")
        while self.in_call and not self.stop_audio.is_set():
            if self.input_stream:
                try:
                    data = self.input_stream.read(CHUNK, exception_on_overflow=False)
                    if data:
                        if self.actual_input_rate != SAMPLE_RATE:
                            data = resample_pcm16(data, self.actual_input_rate, SAMPLE_RATE)
                        await websocket.send(data)
                except Exception as e:
                    logger.debug(f"Audio read error: {e}")
            await asyncio.sleep(0.005)

    async def handle_call(self, msg, websocket):
        """Processes call signaling messages."""
        m_type = msg.get("type")
        caller = msg.get("caller_name", "Doctor / Guardian")

        if m_type == "call_invite":
            logger.info(f"Incoming call from: {caller}")
            self.current_caller = caller
            self.play_chime("ring")

            # Auto-accept after 1 second chime
            await asyncio.sleep(1)
            self.in_call = True
            if self.on_call_status_change:
                try: self.on_call_status_change(True)
                except Exception: pass
            self.stop_audio.clear()
            self.start_audio_streams()

            accept_msg = {
                "type": "call_accept",
                "target": msg.get("caller_id"),
                "sender": DEVICE_IDENTITY,
                "patient_code": self.patient_code,
                "message": "In-home companion connected and microphone live"
            }
            await websocket.send(json.dumps(accept_msg))
            logger.info(f"Call connected with {caller}!")

            # Launch microphone sender task
            asyncio.create_task(self.mic_sender(websocket))

        elif m_type == "call_end":
            logger.info("Call ended by remote caller.")
            self.in_call = False
            if self.on_call_status_change:
                try: self.on_call_status_change(False)
                except Exception: pass
            self.stop_audio_streams()
            self.play_chime("hangup")

        elif m_type == "audio_frame" and self.in_call:
            raw = msg.get("raw_bytes")
            if self.output_stream and raw:
                try:
                    to_play = bytes(raw)
                    if self.actual_output_rate != SAMPLE_RATE:
                        to_play = resample_pcm16(to_play, SAMPLE_RATE, self.actual_output_rate)
                    self.output_stream.write(to_play)
                except Exception as e:
                    logger.debug(f"Speaker write error: {e}")

    async def run(self):
        """Main connection and auto-reconnect loop with HTTPS/WSS auto-negotiation."""
        logger.info(f"Starting BaymaxCare Voice Companion Client for {self.patient_code}")

        endpoints = [
            (f"wss://{self.host}:{self.port}/ws/stream", get_ssl_context(), "Secure WSS"),
            (f"ws://{self.host}:{self.port}/ws/stream", None, "Standard WS")
        ]

        while self.running:
            connected = False
            for url, ssl_ctx, proto_name in endpoints:
                if not self.running:
                    break
                try:
                    logger.info(f"Attempting {proto_name} connection to {url}...")
                    async with websockets.connect(url, ssl=ssl_ctx) as ws:
                        self.ws = ws
                        connected = True
                        logger.info(f"Connected to BaymaxCare Telehealth Server via {proto_name}!")

                        # Register companion device
                        await ws.send(json.dumps({
                            "type": "companion_online",
                            "device": DEVICE_IDENTITY,
                            "patient_code": self.patient_code,
                            "status": "ready_for_calls"
                        }))

                        async for message in ws:
                            if isinstance(message, bytes):
                                if self.in_call and self.output_stream:
                                    try:
                                        to_play = message
                                        if self.actual_output_rate != SAMPLE_RATE:
                                            to_play = resample_pcm16(message, SAMPLE_RATE, self.actual_output_rate)
                                        self.output_stream.write(to_play)
                                    except Exception as e:
                                        logger.debug(f"Speaker write error: {e}")
                            else:
                                try:
                                    msg = json.loads(message)
                                    await self.handle_call(msg, ws)
                                except json.JSONDecodeError:
                                    pass

                except (websockets.ConnectionClosed, websockets.WebSocketException, ConnectionRefusedError, OSError, Exception) as e:
                    logger.warning(f"{proto_name} connection attempt failed: {e}")
                    self.in_call = False
                    self.stop_audio_streams()

                if connected:
                    break

            if self.running:
                logger.info("Will attempt reconnection in 3 seconds...")
                await asyncio.sleep(3)

    def shutdown(self):
        """Cleanly stops client and streams."""
        self.running = False
        self.in_call = False
        self.stop_audio_streams()
        if self.audio:
            try:
                self.audio.terminate()
            except Exception:
                pass
        logger.info("[VoiceCall] Companion voice client stopped.")


_companion_instance = None
_companion_thread = None

def start_in_background(host=SERVER_HOST, port=SERVER_PORT, patient_code=PATIENT_CODE, on_call_status_change=None):
    """Starts the 2-Way Voice Call Companion in a background daemon thread."""
    global _companion_instance, _companion_thread

    def _thread_target():
        asyncio.run(_companion_instance.run())

    _companion_instance = BaymaxVoiceCompanion(
        host=host,
        port=port,
        patient_code=patient_code,
        on_call_status_change=on_call_status_change
    )
    _companion_thread = threading.Thread(target=_thread_target, daemon=True)
    _companion_thread.start()
    logger.info(f"[VoiceCall] 2-Way Voice Calling listening in background for patient {patient_code}")
    return _companion_instance


if __name__ == "__main__":
    companion = BaymaxVoiceCompanion()
    try:
        asyncio.run(companion.run())
    except KeyboardInterrupt:
        logger.info("Voice companion shut down.")
        companion.stop_audio_streams()
