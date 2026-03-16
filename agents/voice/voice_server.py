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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agents.ddi_loader import load_ddinter
from agents.dgidb_loader import load_dgidb
from agents.voice.pgx_voice_agent import pgx_voice_agent, APP_NAME

print("🔄 Loading PGx data...")
load_ddinter("data/ddinter")
load_dgidb("data/dgidb/interactions.tsv")
print("✅ Data ready\n")

# ── Gemini client for transcript correction ──────────────────────────────
from google import genai
from google.genai import types as genai_types

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
            "8. NEVER hallucinate or invent drug or gene names that are NOT phonetically present in the garbled transcription. If you cannot phonetically trace a drug/gene name back to specific sounds in the garbled text, do NOT include it. Only include a drug/gene if the agent explicitly named it in its response OR you can clearly hear it in the garbled text.\n"
            "9. If the transcription contains ONLY background noise, baby sounds, non-clinical chatter, or no pharmacogenomics-related content, reply with exactly: NONE\n"
            "10. Output a single clean sentence. No quotes, no explanation, no preamble. Or NONE if no clinical speech detected.\n\n"

            f"GARBLED TRANSCRIPTION: {garbled}\n"
            f"AGENT'S RESPONSE: {agent_response}\n\n"
            "CORRECTED TRANSCRIPT:"
        )
        response = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model=CORRECTION_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                max_output_tokens=150,
                thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
                temperature=0.1,
            ),
        )
        corrected = response.text
        if corrected:
            corrected = corrected.strip().strip('"').strip("'").strip()
            # Skip if LLM couldn't decode anything clinical
            skip_phrases = ["no speech", "no clinical", "background noise", "not detected",
                           "unclear", "inaudible", "no relevant", "[", "none"]
            if corrected and not any(s in corrected.lower() for s in skip_phrases):
                return corrected
    except Exception as e:
        print(f"⚠️ Transcript correction failed: {e}")
    return None

app = FastAPI()

@app.get("/")
async def root():
    return FileResponse("voice_ui.html")

@app.get("/voice_ui.html")
async def voice_ui():
    return FileResponse("voice_ui.html")
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

    async def upstream_with_queue(queue):
        """Route WebSocket messages to the given queue."""
        try:
            while True:
                message = await websocket.receive()
                if "text" in message:
                    data = json.loads(message["text"])
                    if data.get("type") == "text":
                        queue.send_content(
                            types.Content(role="user", parts=[types.Part(text=data["text"])])
                        )
                    elif data.get("type") == "end":
                        queue.close()
                        break
                elif "bytes" in message:
                    queue.send_realtime(
                        types.Blob(data=message["bytes"], mime_type="audio/pcm;rate=16000")
                    )
        except WebSocketDisconnect:
            queue.close()

    async def upstream():
        await upstream_with_queue(live_request_queue)

    async def downstream():
        import base64

        MAX_RETRIES = 3
        retry_count = 0

        # Persistent state across retries
        turn_counter = 0
        conversation_context = {
            "medications": [],
            "genes": [],
            "last_report": "",
        }
        # Track pending tool call — so we can replay it after reconnect
        pending_tool_call = {
            "medications": None,
            "genotypes": None,
        }

        while retry_count <= MAX_RETRIES:
            # Per-attempt state
            input_transcript_chunks = []
            output_transcript_chunks = []
            early_correction_fired = False
            agent_is_responding = False

            async def send_correction(garbled, agent_hint, turn_id):
                """Fire LLM correction and send result to client."""
                try:
                    corrected = await correct_transcript(garbled, agent_hint)
                    if corrected:
                        print(f"📝 Turn {turn_id} corrected: '{garbled[:50]}...' → '{corrected}'")
                        await websocket.send_text(json.dumps({
                            "type": "corrected_transcript",
                            "turn_id": turn_id,
                            "original": garbled,
                            "text": corrected
                        }))
                except Exception as e:
                    print(f"⚠️ Correction failed: {e}")

            try:
                # If this is a retry, create a new session with context summary
                if retry_count > 0:
                    nonlocal session, live_request_queue
                    new_session_id = f"{session_id}_retry{retry_count}"
                    session = await session_service.create_session(
                        app_name=APP_NAME,
                        user_id=user_id,
                        session_id=new_session_id
                    )
                    live_request_queue = LiveRequestQueue()

                    # Notify the UI
                    await websocket.send_text(json.dumps({
                        "type": "connection_recovered",
                        "message": "Connection recovered. Resuming session."
                    }))
                    print(f"🔄 Retry {retry_count}: new session {new_session_id}")

                    # Replay pending tool call if connection dropped mid-analysis
                    if pending_tool_call["medications"] is not None:
                        meds = pending_tool_call["medications"]
                        geno = pending_tool_call["genotypes"] or ""
                        print(f"🔁 Replaying interrupted analysis: meds={meds!r} geno={geno!r}")
                        replay_msg = (
                            f"The connection was interrupted during analysis. "
                            f"Please immediately call analyze_medications with "
                            f"medications='{meds}' and genotypes='{geno}'. "
                            f"Do not ask for confirmation — run the analysis now."
                        )
                        live_request_queue.send_content(
                            types.Content(role="user", parts=[types.Part(text=replay_msg)])
                        )
                    elif conversation_context["medications"] or conversation_context["genes"]:
                        # No pending call — just restore context summary
                        ctx = conversation_context
                        summary = "Previous conversation context: "
                        if ctx["medications"]:
                            summary += f"Patient medications: {', '.join(ctx['medications'])}. "
                        if ctx["genes"]:
                            summary += f"Patient genetic variants: {', '.join(ctx['genes'])}. "
                        if ctx["last_report"]:
                            summary += f"Last analysis result: {ctx['last_report'][:200]}"
                        live_request_queue.send_content(
                            types.Content(role="user", parts=[types.Part(text=summary)])
                        )

                    # Restart upstream for the new queue
                    asyncio.create_task(upstream_with_queue(live_request_queue))

                async for event in runner.run_live(
                    user_id=user_id,
                    session_id=session.id,
                    live_request_queue=live_request_queue,
                    run_config=run_config,
                ):
                    payload = event.model_dump(mode="json", exclude_none=True)
                    payload["_turn_id"] = turn_counter
                    await websocket.send_text(json.dumps(payload))

                    # DEBUG: print any event that mentions function/tool
                    payload_str = json.dumps(payload)
                    if "function" in payload_str.lower() or "tool" in payload_str.lower() or "META" in payload_str:
                        print(f"🔍 TOOL EVENT keys={list(payload.keys())} snippet={payload_str[:300]}")

                    # ── Accumulate input transcription chunks (ONLY before agent responds)
                    if hasattr(event, "input_transcription") and event.input_transcription:
                        if event.input_transcription.text and not agent_is_responding:
                            input_transcript_chunks.append(event.input_transcription.text)

                    # ── Accumulate output transcription chunks
                    if hasattr(event, "output_transcription") and event.output_transcription:
                        if event.output_transcription.text:
                            agent_is_responding = True
                            output_transcript_chunks.append(event.output_transcription.text)

                    # ── Check for function calls and responses
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.function_call and part.function_call.name == "analyze_medications":
                                args = part.function_call.args or {}
                                print(f"🔬 Tool call: meds='{args.get('medications', '')}', geno='{args.get('genotypes', '')}'")
                                # Save args so we can replay if connection drops before response
                                pending_tool_call["medications"] = args.get("medications", "")
                                pending_tool_call["genotypes"] = args.get("genotypes", "")

                            if part.function_response:
                                # Tool completed — clear pending state
                                pending_tool_call["medications"] = None
                                pending_tool_call["genotypes"] = None
                                raw_result = str(part.function_response.response.get("result", ""))
                                if raw_result:
                                    report_text = raw_result
                                    if "|||META|||" in raw_result:
                                        report_text, meta_str = raw_result.split("|||META|||", 1)
                                        report_text = report_text.strip()
                                        try:
                                            meta = json.loads(meta_str)
                                            if meta.get("__pgx_meta__"):
                                                drugs = meta.get("drugs", [])
                                                genes = meta.get("genes", [])
                                                print(f"📋 Resolved: drugs={drugs}, genes={genes}")
                                                # Update persistent context
                                                conversation_context["medications"] = drugs
                                                conversation_context["genes"] = genes
                                                conversation_context["last_report"] = report_text[:300]
                                                if drugs or genes:
                                                    await websocket.send_text(json.dumps({
                                                        "type": "context_update",
                                                        "medications": drugs,
                                                        "genes": [g.upper() for g in genes]
                                                    }))
                                        except json.JSONDecodeError:
                                            pass

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

                    # ── EARLY FIRE: once we have 3+ output chunks
                    if (not early_correction_fired
                        and len(output_transcript_chunks) >= 3
                        and input_transcript_chunks):
                        early_correction_fired = True
                        garbled = " ".join(input_transcript_chunks).strip()
                        hint = " ".join(output_transcript_chunks).strip()
                        asyncio.create_task(
                            send_correction(garbled, hint, turn_counter)
                        )

                    # ── On turn_complete
                    if hasattr(event, "turn_complete") and event.turn_complete:
                        garbled_input = " ".join(input_transcript_chunks).strip()
                        agent_output = " ".join(output_transcript_chunks).strip()
                        current_turn = turn_counter

                        if garbled_input:
                            asyncio.create_task(
                                send_correction(garbled_input, agent_output, current_turn)
                            )

                        # Reset per-turn state
                        input_transcript_chunks = []
                        output_transcript_chunks = []
                        early_correction_fired = False
                        agent_is_responding = False
                        turn_counter += 1

                # If we get here cleanly, the stream ended normally
                break

            except Exception as e:
                retry_count += 1
                print(f"⚠️ Live flow error (attempt {retry_count}/{MAX_RETRIES}): {e}")
                if retry_count > MAX_RETRIES:
                    print("❌ Max retries exceeded. Ending session.")
                    try:
                        await websocket.send_text(json.dumps({
                            "type": "connection_lost",
                            "message": "Connection lost permanently. Please refresh the page."
                        }))
                    except:
                        pass
                    break
                else:
                    try:
                        await websocket.send_text(json.dumps({
                            "type": "connection_recovering",
                            "message": f"Connection interrupted. Reconnecting (attempt {retry_count})…"
                        }))
                    except:
                        break
                    await asyncio.sleep(1)  # Brief pause before retry

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
