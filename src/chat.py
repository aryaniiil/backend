# backend/src/chat.py

from fastapi import APIRouter, HTTPException, File, UploadFile, Form
from pydantic import BaseModel
import os
import requests
import base64
from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime, timezone
from typing import List, Dict, Any

# Load environment variables
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
IMGBB_API_KEY = os.getenv("IMGBB_API_KEY")

# --- MongoDB Connection ---
try:
    client = MongoClient(MONGO_URI)
    auth_db = client.mobileauth
    users_collection = auth_db.users
    sessions_collection = auth_db.sessions
    chats_db = client.chats
    print("Successfully connected to MongoDB.")
except Exception as e:
    print(f"Error connecting to MongoDB: {e}")
    client = None 

router = APIRouter()

# --- Pydantic Models ---

# FIX: Define a specific model for the send_message request body
class SendMessageRequest(BaseModel):
    sessionId: str
    text: str

class MessageResponse(BaseModel):
    id: str
    text: str
    timestamp: datetime
    isUser: bool

class StatusResponse(BaseModel):
    status: str
    message: str

# --- Helper Function ---
def find_user_by_session(session_id: str) -> Dict[str, Any] | None:
    if not client:
        raise HTTPException(status_code=503, detail="Database connection not available")
    user = None
    if session_id.startswith('user_'):
        user = users_collection.find_one({"clerkSessionId": session_id})
    else:
        session_record = sessions_collection.find_one({"sessionId": session_id, "verified": True})
        if session_record:
            mobile_number = session_record.get("mobileNumber")
            user = users_collection.find_one({"mobileNumber": mobile_number})
    return user

# --- API Endpoints ---

@router.get("/chat/history/{session_id}", response_model=List[MessageResponse])
def get_chat_history(session_id: str):
    user = find_user_by_session(session_id)
    if not user:
        return []

    chat_collection = chats_db[f"chat_{user['_id']}"]
    
    messages_cursor = chat_collection.find().sort("timestamp", 1)
    history = [
        MessageResponse(
            id=str(msg["_id"]),
            text=msg["text"],
            timestamp=msg["timestamp"],
            isUser=msg["sender"] == "user"
        ) for msg in messages_cursor
    ]
    return history

# FIX: Use the new SendMessageRequest model as the type hint
@router.post("/chat/send-message", response_model=StatusResponse)
def send_message(request: SendMessageRequest):
    if not client:
        raise HTTPException(status_code=503, detail="Database not connected")
    
    user = find_user_by_session(request.sessionId)
    if not user:
        raise HTTPException(status_code=404, detail="User not found for the given session.")

    chat_collection = chats_db[f"chat_{user['_id']}"]

    user_message_doc = {"text": request.text, "sender": "user", "timestamp": datetime.now(timezone.utc)}
    chat_collection.insert_one(user_message_doc)

    if chat_collection.count_documents({"sender": "user"}) == 1:
        bot_welcome_text = "Thank you for contacting support! An agent will be with you shortly."
        bot_message_doc = {"text": bot_welcome_text, "sender": "bot", "timestamp": datetime.now(timezone.utc)}
        chat_collection.insert_one(bot_message_doc)
        return StatusResponse(status="ok", message="First message received and bot replied.")

    return StatusResponse(status="ok", message="Message received.")


@router.post("/chat/upload-image", response_model=StatusResponse)
async def upload_image(sessionId: str = Form(...), file: UploadFile = File(...)):
    if not IMGBB_API_KEY:
        raise HTTPException(status_code=500, detail="Image upload service is not configured.")

    user = find_user_by_session(sessionId)
    if not user:
        raise HTTPException(status_code=404, detail="User session not found.")

    try:
        contents = await file.read()
        base64_image = base64.b64encode(contents).decode('utf-8')

        response = requests.post(
            "https://api.imgbb.com/1/upload",
            data={"key": IMGBB_API_KEY, "image": base64_image}
        )
        response.raise_for_status()
        
        image_url = response.json()["data"]["url"]

        chat_collection = chats_db[f"chat_{user['_id']}"]
        image_message_doc = {
            "text": image_url,
            "sender": "user",
            "timestamp": datetime.now(timezone.utc)
        }
        chat_collection.insert_one(image_message_doc)

        return StatusResponse(status="ok", message="Image uploaded successfully.")

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail="Failed to upload image to hosting service.")
    except Exception as e:
        raise HTTPException(status_code=500, detail="An internal error occurred during image upload.")
