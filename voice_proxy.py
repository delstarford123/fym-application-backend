import json
import requests
import os
from geventwebsocket.websocket import WebSocket

class VoiceProxy:
    """
    Proxies voice generation requests to a dedicated GPU microservice.
    This ensures that the main Gevent event loop is not blocked by ML inference.
    """
    
    def __init__(self):
        self.microservice_url = os.getenv('XTTS_MICROSERVICE_URL', 'http://internal-gpu-worker:8000/tts_stream')

    def stream_voice_note(self, ws: WebSocket, text: str, user_id: str = "default"):
        """
        Proxies the text to the XTTS microservice and pipes back audio chunks.
        """
        payload = {
            "text": text,
            "language": "en",
            "speaker_wav": f"merchants/{user_id}/voice_sample.wav" # Path for voice cloning
        }

        # 1. Signal the Flutter client to prepare the audio player
        ws.send(json.dumps({"type": "audio_start", "sample_rate": 24000}))

        try:
            # 2. Open a streaming connection to the XTTS Microservice
            # requests is monkey-patched by gevent, so this is non-blocking
            print(f"[VOICE] Requesting stream from {self.microservice_url}...")
            with requests.post(self.microservice_url, json=payload, stream=True, timeout=30) as response:
                response.raise_for_status()
                
                # 3. Read and pipe chunks
                for chunk in response.iter_content(chunk_size=4096):
                    if chunk:
                        # Pipe raw audio bytes directly to the Flutter WebSocket
                        ws.send(chunk)
                        
        except Exception as e:
            print(f"[ERROR] Voice proxy failure: {e}")
            ws.send(json.dumps({
                "type": "error", 
                "message": "Voice synthesis temporarily unavailable."
            }))
        
        finally:
            # 4. Signal the client that the stream is complete
            ws.send(json.dumps({"type": "audio_end"}))
            print("[VOICE] Proxy stream completed.")
