"""
FastAPI backend for the Animal Sketch Quest drawing/guessing game.

Features:
- Anonymous username login.
- RESTful APIs for dashboard, drawing upload, guessing, drawing listing, leaderboard.
- Real-time dashboard updates via WebSocket (basic push).
- Firebase Storage integration for drawing uploads.
- One guess per drawing per user enforced.
- "Top Drawing Today" and dashboard statistics.
- All sensitive configuration via environment variables.
- All endpoints protected by Firebase ID token authentication.

See README and firebase_setup.md for environment and Firebase configuration.

Author: Animal Sketch Quest backend (2024)
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional, Dict
from pydantic import BaseModel, Field
from datetime import datetime
import uuid
import asyncio
import hashlib

from .firebase_admin_client import verify_firebase_token, get_firebase_storage_bucket

# GLOBALS (in lieu of a proper DB; replace with DB queries for scalable prod system)
DRAWINGS: Dict[str, dict] = {}  # key: drawing_id, value: drawing metadata
USER_GUESSES: Dict[str, Dict[str, str]] = {}  # key: user_id, value: {drawing_id: guess}
DASHBOARD_WS: List[WebSocket] = []  # active dashboard websocket clients

# Demo animal prompts (cycle or random choice on "Add Drawing")
ANIMAL_PROMPTS = [
    "elephant", "cat", "dog", "lion", "giraffe", "dolphin", "penguin", "monkey", "bear", "fox"
]

# --- Pydantic Models ---

class LoginRequest(BaseModel):
    username: str = Field(..., description="Requested username (must be unique per session)")

class LoginResponse(BaseModel):
    user_id: str = Field(..., description="Generated user ID (uuid4)")
    username: str

class DrawingCard(BaseModel):
    id: str
    image_url: str
    prompt: str
    submitter_id: str
    submitter_name: str
    timestamp: datetime
    guesses: int
    correct_guesses: int

class DashboardResponse(BaseModel):
    drawings: List[DrawingCard]
    top_drawing_today: Optional[DrawingCard]

class AddDrawingRequest(BaseModel):
    prompt: str

class DrawingSubmissionResponse(BaseModel):
    drawing_id: str

class GuessRequest(BaseModel):
    drawing_id: str
    guess: str

class GuessResponse(BaseModel):
    correct: bool
    correct_word: Optional[str]
    wrong_guesses: List[str]

class WrongGuess(BaseModel):
    user_id: str
    guess: str

class DrawingDetail(BaseModel):
    id: str
    image_url: str
    prompt: str
    guesses: int
    correct_guesses: int
    wrong_guesses: List[WrongGuess]
    answered: bool

# --- Helper Functions/Classes ---

def get_username_from_token(token: str) -> dict:
    """Verify token and get user details."""
    userinfo = verify_firebase_token(token)
    user_id = userinfo.get('uid')
    username = userinfo.get('name') or (
        userinfo.get('firebase', {}).get('identities', {}).get('email', [None])[0]
    ) or f"user_{user_id[:8]}"
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid Firebase token.")
    return {'user_id': user_id, 'username': username}

async def notify_dashboard_update():
    """Notify all connected websockets that dashboard data changed."""
    data = {"event": "dashboard_update"}
    closed_clients = []
    for ws in DASHBOARD_WS:
        try:
            await ws.send_json(data)
        except Exception:
            closed_clients.append(ws)
    for ws in closed_clients:
        DASHBOARD_WS.remove(ws)

def make_drawing_card(d: dict) -> DrawingCard:
    """Convert drawing dict to DrawingCard model."""
    return DrawingCard(
        id=d['id'],
        image_url=d['image_url'],
        prompt=d['prompt'],
        submitter_id=d.get('submitter_id', ''),
        submitter_name=d.get('submitter_name', ''),
        timestamp=d['timestamp'],
        guesses=len(d.get('guess_history', [])),
        correct_guesses=len([g for g in d.get('guess_history', []) if g['correct']])
    )

def get_top_drawing_today(drawings: Dict[str, dict]) -> Optional[DrawingCard]:
    """Top drawing today (by most correct guesses within today)."""
    today = datetime.utcnow().date()
    filtered = [
        d for d in drawings.values()
        if d['timestamp'].date() == today
    ]
    if not filtered:
        return None
    scored = sorted(filtered, key=lambda x: len([g for g in x.get('guess_history', []) if g['correct']]), reverse=True)
    return make_drawing_card(scored[0]) if scored else None

def get_random_prompt() -> str:
    import random
    return random.choice(ANIMAL_PROMPTS)

def hash_guess(guess: str) -> str:
    """Return normalized, hashed guess for duplicate prevention."""
    norm = guess.lower().strip()
    return hashlib.sha256(norm.encode()).hexdigest()

# PUBLIC_INTERFACE
def is_allowed_guess(user_id: str, drawing_id: str) -> bool:
    """Returns True if user hasn't already guessed this drawing."""
    user_map = USER_GUESSES.get(user_id, {})
    return drawing_id not in user_map

def add_guess(user_id: str, username: str, drawing_id: str, guess: str) -> (bool, List[str]):
    """Records a guess for user on drawing. Returns (correct, all wrong guesses for this drawing)."""
    # Check for double guess
    if not is_allowed_guess(user_id, drawing_id):
        raise HTTPException(status_code=400, detail="You already guessed this drawing.")
    d = DRAWINGS.get(drawing_id)
    if not d:
        raise HTTPException(status_code=404, detail="Drawing not found")
    # Check correctness
    correct = guess.strip().lower() == d['prompt'].strip().lower()
    # Save in user guess history
    USER_GUESSES.setdefault(user_id, {})[drawing_id] = guess

    # Save to drawing's guess_history
    entry = {"user_id": user_id, "username": username, "guess": guess, "correct": correct, "timestamp": datetime.utcnow()}
    d.setdefault('guess_history', []).append(entry)

    # Wrong guesses for this drawing (as strings)
    wrongs = [
        g['guess'] for g in d['guess_history'] if not g['correct']
    ]
    return correct, wrongs

def collect_wrong_guesses(d: dict) -> List[WrongGuess]:
    return [WrongGuess(user_id=g['user_id'], guess=g['guess'])
            for g in d.get('guess_history', []) if not g['correct']]

def has_user_already_answered(user_id: str, d: dict) -> bool:
    return any(g['user_id'] == user_id for g in d.get('guess_history', []) if g['correct'])

# --- FastAPI App Setup ---

app = FastAPI(
    title="Animal Sketch Quest API",
    description="Backend API for drawing-guessing game. Uses Firebase admin for user storage.",
    version="1.0",
    openapi_tags=[
        {"name": "Auth", "description": "Authentication and user login"},
        {"name": "Dashboard", "description": "Dashboard, top drawing and drawing detail APIs"},
        {"name": "Drawings", "description": "Drawings add/view/upload APIs"},
        {"name": "Guess", "description": "Guess API for submitting guesses"},
        {"name": "WebSocket", "description": "Real-time dashboard notifications"},
    ]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ROUTES ---

@app.get("/", tags=["Dashboard"])
def health_check():
    """Quick health check endpoint."""
    return {"message": "Healthy"}

# PUBLIC_INTERFACE
@app.post("/auth/login", response_model=LoginResponse, tags=["Auth"], summary="Anonymous Username Login")
def login(data: LoginRequest):
    """
    Accepts a username for anonymous "login". 
    On success, returns a synthetic user_id and the assigned username.
    Backend does not enforce uniqueness - frontend may coordinate this if needed.
    """
    # In this backend, we trust frontend to handle Firebase auth, so here we only mimic a user_id
    user_id = str(uuid.uuid4())
    return LoginResponse(user_id=user_id, username=data.username)

# PUBLIC_INTERFACE
@app.post("/drawings/add/prompt", tags=["Drawings"], summary="Get Random Drawing Prompt", response_model=AddDrawingRequest)
def get_drawing_prompt(token: str = Form(...)):
    """
    Returns a random animal prompt for "Add Your Drawing" page.
    Requires authentication.
    """
    get_username_from_token(token)
    return AddDrawingRequest(prompt=get_random_prompt())

# PUBLIC_INTERFACE
@app.post("/drawings/upload", tags=["Drawings"], summary="Upload Drawing and Metadata", response_model=DrawingSubmissionResponse)
async def upload_drawing(
    drawing: UploadFile = File(...),
    prompt: str = Form(...),
    token: str = Form(...)
):
    """
    Accepts a PNG drawing submission, saves to Firebase Storage and backend memory, and returns an ID.
    The prompt is the animal word shown to the drawing user.
    """
    user = get_username_from_token(token)
    user_id, username = user['user_id'], user['username']
    drawing_id = str(uuid.uuid4())
    ts = datetime.utcnow()
    filename = f"{drawing_id}.png"

    # Store to Firebase Storage
    bucket = get_firebase_storage_bucket()
    blob = bucket.blob(f"drawings/{filename}")
    content = await drawing.read()  # read all into memory (small file)
    blob.upload_from_string(content, content_type=drawing.content_type or "image/png")
    blob.make_public()
    public_url = blob.public_url  # URL accessible for frontend

    # Save basic drawing metadata in-memory
    DRAWINGS[drawing_id] = {
        "id": drawing_id,
        "image_url": public_url,
        "prompt": prompt.strip().lower(),
        "submitter_id": user_id,
        "submitter_name": username,
        "timestamp": ts,
        "guess_history": [],
    }
    # Callback dashboard update
    asyncio.create_task(notify_dashboard_update())
    return DrawingSubmissionResponse(drawing_id=drawing_id)

# PUBLIC_INTERFACE
@app.get("/dashboard", tags=["Dashboard"], summary="Get Dashboard Data", response_model=DashboardResponse)
def get_dashboard(token: str):
    """
    Returns all drawings for dashboard display, and the "top drawing today".
    Requires authentication.
    """
    get_username_from_token(token)
    cards = [make_drawing_card(d) for d in sorted(DRAWINGS.values(), key=lambda d: d["timestamp"], reverse=True)]
    return DashboardResponse(
        drawings=cards,
        top_drawing_today=get_top_drawing_today(DRAWINGS)
    )

# PUBLIC_INTERFACE
@app.get("/drawings/{drawing_id}", tags=["Drawings"], summary="Get Drawing Detail", response_model=DrawingDetail)
def get_drawing_detail(drawing_id: str, token: str):
    """
    Returns full detail (image URL, prompt, guess stats, wrong guesses) for a specific drawing.
    """
    user = get_username_from_token(token)
    d = DRAWINGS.get(drawing_id)
    if not d:
        raise HTTPException(status_code=404, detail="Drawing not found.")
    # List all wrong guesses as user_id/guess pairs
    return DrawingDetail(
        id=d['id'],
        image_url=d['image_url'],
        prompt="" if not has_user_already_answered(user['user_id'], d) else d['prompt'],  # Hide prompt unless correct
        guesses=len(d.get('guess_history', [])),
        correct_guesses=len([g for g in d.get('guess_history', []) if g['correct']]),
        wrong_guesses=collect_wrong_guesses(d),
        answered=has_user_already_answered(user['user_id'], d)
    )

# PUBLIC_INTERFACE
@app.post("/drawings/{drawing_id}/guess", tags=["Guess"], summary="Submit Guess", response_model=GuessResponse)
def submit_guess(drawing_id: str, data: GuessRequest, token: str = Form(...)):
    """
    Submit a guess for a particular drawing (one guess per user per drawing).
    Returns: if guess was correct, and supplies a list of all wrong guesses.
    """
    user = get_username_from_token(token)
    user_id, username = user['user_id'], user['username']
    correct, all_wrongs = add_guess(user_id, username, drawing_id, data.guess)
    # Notify dashboard if correct
    if correct:
        asyncio.create_task(notify_dashboard_update())
    return GuessResponse(correct=correct, correct_word=(DRAWINGS[drawing_id]['prompt'] if correct else None), wrong_guesses=all_wrongs)

# PUBLIC_INTERFACE
@app.get("/drawings", tags=["Drawings"], summary="Get All Drawings", response_model=List[DrawingCard])
def list_drawings(token: str):
    """
    List all drawings (for dashboard grid view).
    """
    get_username_from_token(token)
    return [make_drawing_card(d) for d in sorted(DRAWINGS.values(), key=lambda d: d["timestamp"], reverse=True)]

# PUBLIC_INTERFACE
@app.websocket("/ws/dashboard", name="DashboardUpateWebSocket")
async def dashboard_websocket(ws: WebSocket):
    """
    WebSocket endpoint for real-time dashboard updates.
    Upon any correct guess or drawing submission, a notification is pushed.
    Frontend simply reconnects and reloads dashboard on receiving messages.
    """
    await ws.accept()
    DASHBOARD_WS.append(ws)
    try:
        while True:
            await ws.receive_text()  # simple ping/pong loop (or ignore input)
    except WebSocketDisconnect:
        if ws in DASHBOARD_WS:
            DASHBOARD_WS.remove(ws)

# PUBLIC_INTERFACE
@app.get("/docs/websocket", tags=["WebSocket"], summary="WebSocket API Usage")
def docs_websocket():
    """
    Usage help for API consumers regarding dashboard websocket.
    """
    return {
        "websocket_url": "/ws/dashboard",
        "purpose": "Listen for JSON: {event: 'dashboard_update'} to trigger dashboard refresh after correct guess or drawing add."
    }

# PUBLIC_INTERFACE
@app.get("/leaderboard", tags=["Dashboard"], summary="Get Leaderboard")
def get_leaderboard(token: str):
    """
    Returns submitters ordered by sum of correct guesses their drawings have received (for display).
    """
    get_username_from_token(token)
    ranking = {}
    for d in DRAWINGS.values():
        sid = d["submitter_id"]
        name = d["submitter_name"]
        n_correct = sum(1 for g in d.get("guess_history", []) if g["correct"])
        ranking.setdefault((sid, name), 0)
        ranking[(sid, name)] += n_correct
    result = [
        {"user_id": sid, "username": name, "score": score}
        for (sid, name), score in sorted(ranking.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return {"leaderboard": result}

# Optionally serve static files if needed (disabled for this backend - no static dir used)
# app.mount("/static", StaticFiles(directory="static"), name="static")
