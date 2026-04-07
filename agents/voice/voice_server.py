import asyncio
import re
import json
import os
import sys
import base64
import secrets
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
sys.path.insert(0, ".")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse
import uvicorn
import httpx
from dotenv import load_dotenv
from google.adk.agents.run_config import RunConfig, StreamingMode, ToolThreadPoolConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.agents import LiveRequestQueue
from google.genai import types

load_dotenv()

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
            skip_phrases = ["no speech", "no clinical", "background noise", "not detected",
                           "unclear", "inaudible", "no relevant", "[", "none"]
            if corrected and not any(s in corrected.lower() for s in skip_phrases):
                return corrected
    except Exception as e:
        print(f"⚠️ Transcript correction failed: {e}")
    return None


# ═══════════════════════════════════════════════════════════════
# APP SETUP
# ═══════════════════════════════════════════════════════════════

app = FastAPI()

# Session middleware MUST be added before Auth0 setup
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", secrets.token_hex(32))
)


# ═══════════════════════════════════════════════════════════════
# AUTH0 SETUP
# ═══════════════════════════════════════════════════════════════

AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "")
AUTH0_CLIENT_ID = os.environ.get("AUTH0_CLIENT_ID", "")
AUTH0_CLIENT_SECRET = os.environ.get("AUTH0_CLIENT_SECRET", "")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000")

def build_auth0_login_url(state: str = "") -> str:
    """Build the Auth0 authorization URL."""
    import urllib.parse
    params = {
        "response_type": "code",
        "client_id": AUTH0_CLIENT_ID,
        "redirect_uri": f"{APP_BASE_URL}/auth/callback",
        "scope": "openid profile email offline_access create:me:connected_accounts read:me:connected_accounts delete:me:connected_accounts",
        "audience": f"https://{AUTH0_DOMAIN}/me/",
    }
    if state:
        params["state"] = state
    return f"https://{AUTH0_DOMAIN}/authorize?" + urllib.parse.urlencode(params)


# ─── Auth routes ──────────────────────────────────────────────

@app.get("/auth/login")
async def auth_login(request: Request):
    """Redirect user to Auth0 login page."""
    state = secrets.token_hex(16)
    request.session["oauth_state"] = state
    return RedirectResponse(url=build_auth0_login_url(state))


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = None, error: str = None):
    """Handle Auth0 callback after login."""
    if error:
        return JSONResponse({"error": error}, status_code=400)
    if not code:
        return JSONResponse({"error": "No code received"}, status_code=400)

    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://{AUTH0_DOMAIN}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": AUTH0_CLIENT_ID,
                "client_secret": AUTH0_CLIENT_SECRET,
                "code": code,
                "redirect_uri": f"{APP_BASE_URL}/auth/callback",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if resp.status_code != 200:
        print(f"❌ Token exchange failed: {resp.text}")
        return JSONResponse({"error": "Token exchange failed", "detail": resp.text}, status_code=400)

    token_data = resp.json()

    # Get user info
    async with httpx.AsyncClient() as client:
        user_resp = await client.get(
            f"https://{AUTH0_DOMAIN}/userinfo",
            headers={"Authorization": f"Bearer {token_data['access_token']}"}
        )
    user_info = user_resp.json()

    # Store in session
    request.session["user"] = user_info
    request.session["access_token"] = token_data.get("access_token")
    request.session["refresh_token"] = token_data.get("refresh_token")

    print(f"✅ User logged in: {user_info.get('email', 'unknown')}")
    
    # If this was a popup connect flow, close the popup
    from fastapi.responses import HTMLResponse
    referer = request.headers.get("referer", "")
    is_connect = "connection=" in str(request.url) or request.session.pop("connect_state", None) is not None
    if is_connect:
        return HTMLResponse("<html><body><script>window.close();</script></body></html>")
    
    return RedirectResponse(url="/")


@app.get("/auth/connect-callback")
async def auth_connect_callback(request: Request, connect_code: str = None, state: str = None, error: str = None):
    """Complete the connected account flow using My Account API."""
    from fastapi.responses import HTMLResponse

    if error:
        print(f"❌ Connect callback error: {error}")
        return HTMLResponse("<html><body><p>Connection failed. Please close this window and try again.</p></body></html>")

    if not connect_code:
        return RedirectResponse(url="/")

    auth_session = request.session.get("connect_auth_session")
    access_token = request.session.get("access_token")

    if not auth_session or not access_token:
        return HTMLResponse("<html><body><p>Session expired. Please try again.</p></body></html>")

    # Step 3: Complete the connection
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://{AUTH0_DOMAIN}/me/v1/connected-accounts/complete",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "auth_session": auth_session,
                "connect_code": connect_code,
                "redirect_uri": f"{APP_BASE_URL}/auth/connect-callback",
            },
        )

    if resp.status_code in (200, 201):
        print(f"✅ Account connected: {resp.json()}")
        request.session.pop("connect_auth_session", None)
        return HTMLResponse("<html><body><script>if(window.opener){window.opener.location.reload();window.close();}else{window.location='/'}</script></body></html>")
    else:
        print(f"❌ Connect complete failed: {resp.status_code} {resp.text}")
        return HTMLResponse("<html><body><script>if(window.opener){window.opener.location.reload();window.close();}else{window.location='/'}</script></body></html>")


async def refresh_access_token(request: Request) -> str | None:
    """Refresh the access token using the refresh token."""
    refresh_token = request.session.get("refresh_token")
    if not refresh_token:
        return None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://{AUTH0_DOMAIN}/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": AUTH0_CLIENT_ID,
                    "client_secret": AUTH0_CLIENT_SECRET,
                    "refresh_token": refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code == 200:
            token_data = resp.json()
            new_token = token_data.get("access_token")
            request.session["access_token"] = new_token
            if token_data.get("refresh_token"):
                request.session["refresh_token"] = token_data["refresh_token"]
            print("✅ Access token refreshed")
            return new_token
    except Exception as e:
        print(f"⚠️ Token refresh failed: {e}")
    return None


@app.get("/auth/logout")
async def auth_logout(request: Request):
    """Log out — clear session and redirect to Auth0 logout."""
    request.session.clear()
    import urllib.parse
    params = urllib.parse.urlencode({
        "client_id": AUTH0_CLIENT_ID,
        "returnTo": APP_BASE_URL,
    })
    return RedirectResponse(url=f"https://{AUTH0_DOMAIN}/v2/logout?{params}")


@app.get("/auth/connect")
async def auth_connect(request: Request, connection: str):
    """Initiate connected account flow using My Account API."""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/auth/login")

    access_token = request.session.get("access_token")
    if not access_token:
        return RedirectResponse(url="/auth/login")

    # Step 1: Call /me/v1/connected-accounts/connect to get the connect URI
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://{AUTH0_DOMAIN}/me/v1/connected-accounts/connect",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "connection": connection,
                "redirect_uri": f"{APP_BASE_URL}/auth/connect-callback",
                "state": secrets.token_hex(16),
                "scopes": [
                    "openid",
                    "https://www.googleapis.com/auth/gmail.send",
                    "https://www.googleapis.com/auth/calendar.events",
                    "https://www.googleapis.com/auth/drive.file",
                    "https://www.googleapis.com/auth/spreadsheets",
                ],
            },
        )

    # If token expired, refresh and retry
    if resp.status_code == 401:
        print("⚠️ Access token expired, refreshing...")
        new_token = await refresh_access_token(request)
        if new_token:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"https://{AUTH0_DOMAIN}/me/v1/connected-accounts/connect",
                    headers={
                        "Authorization": f"Bearer {new_token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json={
                        "connection": connection,
                        "redirect_uri": f"{APP_BASE_URL}/auth/connect-callback",
                        "state": secrets.token_hex(16),
                        "scopes": [
                            "openid",
                            "https://www.googleapis.com/auth/gmail.send",
                            "https://www.googleapis.com/auth/calendar.events",
                            "https://www.googleapis.com/auth/drive.file",
                            "https://www.googleapis.com/auth/spreadsheets",
                        ],
                    },
                )
        else:
            return RedirectResponse(url="/auth/login")

    print(f"🔍 Connect response: {resp.status_code} {resp.text[:300]}")
    if resp.status_code not in (200, 201):
        print(f"❌ Connect initiation failed: {resp.status_code} {resp.text}")
        return JSONResponse({"error": f"Connect failed: {resp.text}"}, status_code=400)

    data = resp.json()
    auth_session = data.get("auth_session")
    connect_uri = data.get("connect_uri")
    ticket = data.get("connect_params", {}).get("ticket")

    print(f"✅ Connect initiated: auth_session={auth_session[:20] if auth_session else None}, ticket={ticket}")

    # Store auth_session for the complete step
    request.session["connect_auth_session"] = auth_session

    # Step 2: Redirect user to the connect URI
    return RedirectResponse(url=f"{connect_uri}?ticket={ticket}")


# ─── User / connection info endpoints ─────────────────────────

@app.get("/api/user")
async def get_user(request: Request):
    """Return current user info for the frontend."""
    user = request.session.get("user")
    if not user:
        return JSONResponse({"authenticated": False}, status_code=401)
    return JSONResponse({"authenticated": True, "user": user})


@app.get("/api/connections")
async def get_connections(request: Request):
    """Return list of connected accounts by testing token exchange."""
    user = request.session.get("user")
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    connections = []

    # Test Google connection
    try:
        await get_token_from_vault(request, "google-oauth2")
        connections.append({"connection": "google-oauth2", "status": "connected"})
    except:
        pass

    # Test Slack connection
    try:
        await get_token_from_vault(request, "sign-in-with-slack")
        connections.append({"connection": "sign-in-with-slack", "status": "connected"})
    except:
        pass

    return JSONResponse({"connections": connections})


# ═══════════════════════════════════════════════════════════════
# TOKEN VAULT — Core token exchange helper
# ═══════════════════════════════════════════════════════════════

async def get_token_from_vault(request: Request, connection: str) -> str:
    """
    Exchange the user's Auth0 refresh token for an external provider token.
    Auto-refreshes the Auth0 refresh token if needed.
    """
    refresh_token = request.session.get("refresh_token")
    if not refresh_token:
        raise Exception("No refresh token in session. User must log in again.")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://{AUTH0_DOMAIN}/oauth/token",
            json={
                "grant_type": "urn:auth0:params:oauth:grant-type:token-exchange:federated-connection-access-token",
                "subject_token": refresh_token,
                "subject_token_type": "urn:ietf:params:oauth:token-type:refresh_token",
                "requested_token_type": "http://auth0.com/oauth/token-type/federated-connection-access-token",
                "client_id": AUTH0_CLIENT_ID,
                "client_secret": AUTH0_CLIENT_SECRET,
                "connection": connection,
            },
            headers={"Content-Type": "application/json"},
        )

        if resp.status_code != 200:
            raise Exception(
                f"Token exchange failed for '{connection}': {resp.status_code} — {resp.text}\n"
                f"Make sure the user has connected their {connection} account."
            )

        return resp.json()["access_token"]


# ═══════════════════════════════════════════════════════════════
# TOKEN VAULT TOOL FUNCTIONS
# ═══════════════════════════════════════════════════════════════

async def send_report_email(request: Request, to_email: str, subject: str, report_html: str) -> dict:
    """Send the safety report via the clinician's Gmail using Token Vault."""
    try:
        google_token = await get_token_from_vault(request, "google-oauth2")

        full_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
body {{ font-family: 'DM Sans', Arial, sans-serif; background: #f8f7f4; color: #1c1c1e; padding: 40px; max-width: 700px; margin: 0 auto; }}
h1 {{ font-family: Georgia, serif; font-size: 22px; color: #1c1c1e; border-bottom: 2px solid #0a6e72; padding-bottom: 12px; margin-bottom: 20px; }}
.meta {{ color: #98989d; font-size: 12px; margin-bottom: 24px; }}
.r-critical {{ background: #fdf2f1; border: 1px solid #f5c2be; border-radius: 8px; padding: 10px 14px; margin: 8px 0; color: #c0392b; font-weight: 600; }}
.r-high {{ background: #fdf6ed; border: 1px solid #f0d4b0; border-radius: 8px; padding: 10px 14px; margin: 8px 0; color: #b7670a; }}
.r-dosing {{ border-left: 3px solid #0a6e72; padding: 7px 12px; margin: 6px 0; color: #0a6e72; background: #e6f4f5; }}
.rline {{ padding: 6px 0; border-bottom: 1px solid #f0ede8; color: #48484a; }}
.footer {{ margin-top: 30px; padding-top: 12px; border-top: 1px solid #e5e2db; color: #98989d; font-size: 11px; }}
</style>
</head>
<body>
<h1>PGx·Guardian Safety Report</h1>
<div class="meta">Generated by PGx-Guardian v3 · {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
{report_html}
<div class="footer">⚠ For clinical decision support only. All recommendations require physician review.</div>
</body>
</html>"""

        message = MIMEMultipart("alternative")
        message["to"] = to_email
        message["subject"] = subject
        message.attach(MIMEText(full_html, "html"))

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={"Authorization": f"Bearer {google_token}", "Content-Type": "application/json"},
                json={"raw": raw},
            )
            if resp.status_code == 200:
                return {"success": True, "message": f"Report emailed to {to_email}"}
            else:
                return {"success": False, "error": resp.text}

    except Exception as e:
        return {"success": False, "error": str(e)}


async def schedule_followup(request: Request, summary: str, date_iso: str, duration_minutes: int = 30) -> dict:
    """Create a follow-up calendar event using the clinician's Google Calendar."""
    try:
        google_token = await get_token_from_vault(request, "google-oauth2")

        from datetime import datetime, timedelta
        start = datetime.fromisoformat(date_iso)
        end = start + timedelta(minutes=duration_minutes)

        event = {
            "summary": summary,
            "description": "PGx-Guardian follow-up review. Auto-scheduled by pharmacogenomics safety agent.",
            "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
            "reminders": {"useDefault": True},
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                headers={"Authorization": f"Bearer {google_token}", "Content-Type": "application/json"},
                json=event,
            )
            if resp.status_code in (200, 201):
                return {"success": True, "message": f"Follow-up scheduled: {summary}"}
            else:
                return {"success": False, "error": resp.text}

    except Exception as e:
        return {"success": False, "error": str(e)}


async def save_report_to_drive(request: Request, report_html: str, filename: str) -> dict:
    """Upload the safety report HTML to Google Drive."""
    try:
        google_token = await get_token_from_vault(request, "google-oauth2")

        metadata = json.dumps({"name": filename, "mimeType": "text/html"})
        boundary = "pgx_guardian_boundary"
        body = (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{metadata}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: text/html\r\n\r\n"
            f"{report_html}\r\n"
            f"--{boundary}--"
        )

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
                headers={
                    "Authorization": f"Bearer {google_token}",
                    "Content-Type": f"multipart/related; boundary={boundary}",
                },
                content=body.encode(),
            )
            if resp.status_code in (200, 201):
                file_data = resp.json()
                return {"success": True, "message": f"Saved to Drive: {filename}", "file_id": file_data.get("id")}
            else:
                return {"success": False, "error": resp.text}

    except Exception as e:
        return {"success": False, "error": str(e)}


async def log_to_audit_sheet(request: Request, spreadsheet_id: str, row_data: list) -> dict:
    """Append a row to the audit Google Sheet."""
    try:
        google_token = await get_token_from_vault(request, "google-oauth2")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/Sheet1!A1:append",
                params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
                headers={"Authorization": f"Bearer {google_token}", "Content-Type": "application/json"},
                json={"values": [row_data]},
            )
            if resp.status_code == 200:
                return {"success": True, "message": "Audit log updated"}
            else:
                return {"success": False, "error": resp.text}

    except Exception as e:
        return {"success": False, "error": str(e)}


async def post_to_slack(request: Request, channel: str, message: str) -> dict:
    """Post a critical alert to a Slack channel."""
    try:
        slack_token = await get_token_from_vault(request, "sign-in-with-slack")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {slack_token}", "Content-Type": "application/json"},
                json={
                    "channel": channel,
                    "text": message,
                    "blocks": [
                        {"type": "header", "text": {"type": "plain_text", "text": "⚠️ PGx-Guardian Critical Alert"}},
                        {"type": "section", "text": {"type": "mrkdwn", "text": message}},
                    ],
                },
            )
            data = resp.json()
            if data.get("ok"):
                return {"success": True, "message": f"Alert posted to #{channel}"}
            else:
                return {"success": False, "error": data.get("error", "Unknown Slack error")}

    except Exception as e:
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# ACTION API ENDPOINTS (called by frontend buttons)
# ═══════════════════════════════════════════════════════════════

@app.post("/api/actions/send-email")
async def action_send_email(request: Request):
    body = await request.json()
    result = await send_report_email(
        request,
        to_email=body["to_email"],
        subject=body.get("subject", "PGx-Guardian Safety Report"),
        report_html=body["report_html"],
    )
    return JSONResponse(result)


@app.post("/api/actions/schedule-followup")
async def action_schedule_followup(request: Request):
    body = await request.json()
    result = await schedule_followup(
        request,
        summary=body["summary"],
        date_iso=body["date_iso"],
        duration_minutes=body.get("duration_minutes", 30),
    )
    return JSONResponse(result)


@app.post("/api/actions/save-to-drive")
async def action_save_to_drive(request: Request):
    body = await request.json()
    result = await save_report_to_drive(
        request,
        report_html=body["report_html"],
        filename=body.get("filename", "PGx-Guardian-Report.html"),
    )
    return JSONResponse(result)


@app.post("/api/actions/log-audit")
async def action_log_audit(request: Request):
    body = await request.json()
    result = await log_to_audit_sheet(
        request,
        spreadsheet_id=body["spreadsheet_id"],
        row_data=body["row_data"],
    )
    return JSONResponse(result)


@app.post("/api/actions/slack-alert")
async def action_slack_alert(request: Request):
    body = await request.json()
    result = await post_to_slack(
        request,
        channel=body["channel"],
        message=body["message"],
    )
    return JSONResponse(result)


# ═══════════════════════════════════════════════════════════════
# MAIN ROUTES (unchanged — but now require login)
# ═══════════════════════════════════════════════════════════════

@app.get("/")
async def root(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/auth/login")
    return FileResponse("voice_ui.html")

@app.get("/voice_ui.html")
async def voice_ui(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/auth/login")
    return FileResponse("voice_ui.html")


# ═══════════════════════════════════════════════════════════════
# VOICE / WEBSOCKET (completely unchanged from your original)
# ═══════════════════════════════════════════════════════════════

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
                                pending_tool_call["medications"] = args.get("medications", "")
                                pending_tool_call["genotypes"] = args.get("genotypes", "")

                            if part.function_response:
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

                        input_transcript_chunks = []
                        output_transcript_chunks = []
                        early_correction_fired = False
                        agent_is_responding = False
                        turn_counter += 1

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
                    await asyncio.sleep(1)

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
    print("PGx-Guardian v3 Voice Server starting...")
    print("Server on http://localhost:8000")
    print("=" * 55)
    uvicorn.run(app, host="0.0.0.0", port=8000)
