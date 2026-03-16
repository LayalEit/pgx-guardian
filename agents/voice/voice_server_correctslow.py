import asyncio
import re
import json
import os
import sys
sys.path.insert(0, ".")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn
from dotenv import load_dotenv
from google.adk.agents.run_config import RunConfig, StreamingMode, ToolThreadPoolConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.agents import LiveRequestQueue
from google.genai import types

load_dotenv()

from agents.ddi_loader import load_ddinter
from agents.dgidb_loader import load_dgidb
from agents.voice.pgx_voice_agent import pgx_voice_agent, APP_NAME

print("🔄 Loading PGx data...")
load_ddinter("data/ddinter")
load_dgidb("data/dgidb/interactions.tsv")
print("✅ Data ready\n")

# ── Gemini client for transcript correction ──────────────────────────────
from google import genai

gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
CORRECTION_MODEL = "gemini-2.5-flash"

async def correct_transcript(garbled: str, agent_response: str) -> str | None:
    """Use Gemini Flash to reconstruct what the user actually said."""
    if not garbled or not garbled.strip():
        return None
    try:
        prompt = (
            "You are a transcript correction module inside a pharmacogenomics clinical decision support system called PGx-Guardian.\n\n"

            "TASK: Reconstruct the clinician's original spoken sentence from a garbled voice-to-text transcription.\n\n"

            "CONTEXT:\n"
            "- A clinician is verbally describing a patient's medications and genetic profile to the system.\n"
            "- The domain is strictly pharmacogenomics: drug names, gene names, allele variants, phenotypes, and clinical instructions.\n"
            "- The speech-to-text engine is extremely low quality and produces phonetic approximations of medical terms.\n\n"

            "COMMON MISRECOGNITIONS:\n"
            "- Drug names get split into nonsense syllables or replaced by common English words that sound similar.\n"
            "  Examples: 'ibuprofen' → 'evil pro phet' / 'able profit' / 'I view pro fen'\n"
            "           'paracetamol' → 'parts more' / 'para see tamo' / 'para settle'\n"
            "           'clopidogrel' → 'clop idle grill' / 'clip a dog rel'\n"
            "           'omeprazole' → 'oh me pra zol' / 'home episode'\n"
            "           'azathioprine' → 'as a thigh oh preen'\n"
            "- Gene names get mangled: 'CYP2D6' → 'sigh p 2 d 6', 'TP53' → 'D P 53' / 'tea p 53', 'BRCA1' → 'burka one'\n"
            "- Allele notations get broken: '*4/*4' → 'star 4 star 4'\n\n"

            "CRITICAL RULES:\n"
            "1. EVERY word the clinician says is about pharmacogenomics. Interpret ALL words through this lens.\n"
            "2. IGNORE background noise: baby sounds, coughs, random words from other people, TV audio — discard anything that is clearly not the clinician speaking about the patient.\n"
            "3. Use the agent's response as a strong hint — the agent correctly understood the audio even though the transcription is garbled.\n"
            "4. Preserve the clinician's full intent — not just drug/gene names but the complete clinical statement (e.g. 'the patient is taking X and Y with mutations in Z').\n"
            "5. Use standard drug names (lowercase: ibuprofen, paracetamol, clopidogrel) and standard gene names (uppercase: CYP2D6, TP53, BRCA1).\n"
            "6. If the agent's response is generic (e.g. 'based on the provided medications'), rely more heavily on phonetic decoding of the garbled text.\n"
            "7. NEVER summarize or generalize. NEVER replace specific drug/gene names with generic phrases like 'two medications' or 'some drugs'. Always decode the actual specific names from the garbled text.\n"
            "8. NEVER hallucinate drug or gene names that aren't phonetically present in the garbled text. Only include names you can trace back to sounds in the transcription.\n"
            "9. Output a single clean sentence. No quotes, no explanation, no preamble.\n\n"

            f"GARBLED TRANSCRIPTION: {garbled}\n"
            f"AGENT'S RESPONSE: {agent_response}\n\n"
            "CORRECTED TRANSCRIPT:"
        )
        response = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model=CORRECTION_MODEL,
            contents=prompt,
        )
        corrected = response.text
        if corrected:
            corrected = corrected.strip().strip('"').strip("'").strip()
            if corrected:
                return corrected
    except Exception as e:
        print(f"⚠️ Transcript correction failed: {e}")
    return None

app = FastAPI()
session_service = InMemorySessionService()
runner = Runner(app_name=APP_NAME, agent=pgx_voice_agent, session_service=session_service)

@app.websocket("/ws/{user_id}/{session_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str, session_id: str):
    await websocket.accept()
    print(f"Client connected: user={user_id} session={session_id}")

    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id
    )

    run_config = RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede")
            )
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        tool_thread_pool_config=ToolThreadPoolConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    live_request_queue = LiveRequestQueue()

    async def upstream():
        try:
            while True:
                message = await websocket.receive()
                if "text" in message:
                    data = json.loads(message["text"])
                    if data.get("type") == "text":
                        live_request_queue.send_content(
                            types.Content(role="user", parts=[types.Part(text=data["text"])])
                        )
                    elif data.get("type") == "end":
                        live_request_queue.close()
                        break
                elif "bytes" in message:
                    live_request_queue.send_realtime(
                        types.Blob(data=message["bytes"], mime_type="audio/pcm;rate=16000")
                    )
        except WebSocketDisconnect:
            live_request_queue.close()

    async def downstream():
        import base64

        # Accumulate transcripts per turn
        input_transcript_chunks = []
        output_transcript_chunks = []
        turn_counter = 0

        try:
            async for event in runner.run_live(
                user_id=user_id,
                session_id=session.id,
                live_request_queue=live_request_queue,
                run_config=run_config,
            ):
                payload = event.model_dump(mode="json", exclude_none=True)
                payload["_turn_id"] = turn_counter
                await websocket.send_text(json.dumps(payload))

                # ── Accumulate input transcription chunks
                if hasattr(event, "input_transcription") and event.input_transcription:
                    if event.input_transcription.text:
                        input_transcript_chunks.append(event.input_transcription.text)

                # ── Accumulate output transcription chunks
                if hasattr(event, "output_transcription") and event.output_transcription:
                    if event.output_transcription.text:
                        output_transcript_chunks.append(event.output_transcription.text)

                # ── Check for function responses (reports)
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.function_response:
                            report_text = str(part.function_response.response.get("result", ""))
                            if report_text:
                                await websocket.send_text(json.dumps({
                                    "type": "report",
                                    "text": report_text
                                }))

                # ── Send output transcription for live display
                if hasattr(event, "server_content") and event.server_content:
                    sc = event.server_content
                    if hasattr(sc, "output_transcription") and sc.output_transcription:
                        if sc.output_transcription.text:
                            await websocket.send_text(json.dumps({
                                "type": "transcript",
                                "text": sc.output_transcription.text
                            }))

                # ── On turn_complete: correct transcript with FULL agent response
                if hasattr(event, "turn_complete") and event.turn_complete:
                    garbled_input = " ".join(input_transcript_chunks).strip()
                    agent_output = " ".join(output_transcript_chunks).strip()
                    current_turn = turn_counter

                    if garbled_input:
                        # Fire correction as background task so it doesn't block
                        async def send_correction(garbled, agent_hint, turn_id):
                            try:
                                corrected = await correct_transcript(garbled, agent_hint)
                                if corrected:
                                    print(f"📝 Transcript corrected: '{garbled[:60]}...' → '{corrected}'")
                                    await websocket.send_text(json.dumps({
                                        "type": "corrected_transcript",
                                        "turn_id": turn_id,
                                        "original": garbled,
                                        "text": corrected
                                    }))
                            except Exception as e:
                                print(f"⚠️ Correction send failed: {e}")

                        asyncio.create_task(
                            send_correction(garbled_input, agent_output, current_turn)
                        )

                    # Reset for next turn
                    input_transcript_chunks = []
                    output_transcript_chunks = []
                    turn_counter += 1

        except Exception as e:
            print(f"An unexpected error occurred in live flow: {e}")

    try:
        await asyncio.gather(upstream(), downstream())
    except Exception as e:
        print(f"Session error: {e}")
    finally:
        live_request_queue.close()
        print(f"Client disconnected: user={user_id}")

if __name__ == "__main__":
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
    print("PGx-Guardian Voice Server starting...")
    print("Server on http://localhost:8000")
    print("=" * 55)
    uvicorn.run(app, host="0.0.0.0", port=8000)
