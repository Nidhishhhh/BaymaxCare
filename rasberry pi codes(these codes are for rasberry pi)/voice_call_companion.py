"""
BaymaxCare In-Home Companion - 2-Way Voice Call Client
Runs on Raspberry Pi with USB Microphone & Speaker.
Connects with Doctor and Guardian dashboards over WebRTC / WebSocket.
"""

import sys
import time
import json
import logging
import threading
import asyncio
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


class BaymaxVoiceCompanion:
    def __init__(self, host=SERVER_HOST, port=SERVER_PORT, patient_code=PATIENT_CODE):
        self.ws_url = f"ws://{host}:{port}/ws/stream"
        self.patient_code = patient_code
        self.in_call = False
        self.current_caller = None
        self.ws = None
        self.audio = pyaudio.PyAudio() if AUDIO_AVAILABLE else None
        self.input_stream = None
        self.output_stream = None
        self.stop_audio = threading.Event()

    def start_audio_streams(self):
        """Starts microphone recording and speaker playback streams."""
        if not AUDIO_AVAILABLE or not self.audio:
            logger.info("Audio hardware not available; running in test simulation mode.")
            return

        try:
            # Open speaker playback stream
            self.output_stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                output=True,
                frames_per_buffer=CHUNK
            )
            # Open microphone input stream
            self.input_stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK
            )
            logger.info("Microphone and speaker streams activated.")
        except Exception as e:
            logger.error(f"Error opening audio streams: {e}")

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
        # If pyttsx3 or system sound is available:
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.say("Connecting call with your care team.")
            engine.runAndWait()
        except Exception:
            pass

    async def mic_sender(self, websocket):
        """Captures microphone and sends audio frames to the caller."""
        logger.info("Started microphone stream sender...")
        while self.in_call and not self.stop_audio.is_set():
            if self.input_stream:
                try:
                    data = self.input_stream.read(CHUNK, exception_on_overflow=False)
                    payload = {
                        "type": "audio_frame",
                        "sender": DEVICE_IDENTITY,
                        "patient_code": self.patient_code,
                        "raw_bytes": list(data[:64])  # Sample chunk
                    }
                    await websocket.send(json.dumps(payload))
                except Exception as e:
                    logger.debug(f"Audio read error: {e}")
            await asyncio.sleep(0.04)

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
            self.stop_audio_streams()
            self.play_chime("hangup")

        elif m_type == "audio_frame" and self.in_call:
            # Play incoming audio frame on speaker
            raw = msg.get("raw_bytes")
            if self.output_stream and raw:
                try:
                    self.output_stream.write(bytes(raw))
                except Exception as e:
                    logger.debug(f"Speaker write error: {e}")

    async def run(self):
        """Main connection and auto-reconnect loop."""
        logger.info(f"Starting BaymaxCare Voice Companion Client for {self.patient_code}")
        logger.info(f"Connecting to {self.ws_url}...")

        while True:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self.ws = ws
                    logger.info("Connected to BaymaxCare Telehealth Server!")

                    # Register companion device
                    await ws.send(json.dumps({
                        "type": "companion_online",
                        "device": DEVICE_IDENTITY,
                        "patient_code": self.patient_code,
                        "status": "ready_for_calls"
                    }))

                    async for message in ws:
                        try:
                            msg = json.loads(message)
                            await self.handle_call(msg, ws)
                        except json.JSONDecodeError:
                            pass

            except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as e:
                logger.warning(f"Connection lost ({e}). Reconnecting in 3 seconds...")
                self.in_call = False
                self.stop_audio_streams()
                await asyncio.sleep(3)


if __name__ == "__main__":
    companion = BaymaxVoiceCompanion()
    try:
        asyncio.run(companion.run())
    except KeyboardInterrupt:
        logger.info("Voice companion shut down.")
        companion.stop_audio_streams()
