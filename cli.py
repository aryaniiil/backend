import os
import sys
import time
import threading
import re
from pymongo import MongoClient
from dotenv import load_dotenv
from typing import Dict, Any, Optional
from datetime import datetime, timezone

# --- Configuration & Setup ---
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")

class Colors:
    USER, BOT, ADMIN, SYSTEM, RESET = '\033[94m', '\033[92m', '\033[93m', '\033[95m', '\033[0m'
    IMAGE = '\033[96m'

# --- Database Connection ---
def connect_to_db():
    if not MONGO_URI:
        print(f"{Colors.SYSTEM}Error: MONGO_URI not found. Exiting.{Colors.RESET}")
        sys.exit(1)
    try:
        client = MongoClient(MONGO_URI)
        client.admin.command('ping')
        print(f"{Colors.SYSTEM}Successfully connected to MongoDB.{Colors.RESET}")
        return client.mobileauth, client.chats
    except Exception as e:
        print(f"{Colors.SYSTEM}Error connecting to MongoDB: {e}{Colors.RESET}")
        sys.exit(1)

auth_db, chats_db = connect_to_db()
users_collection = auth_db.users
sessions_collection = auth_db.sessions

# --- Core Functions ---

def find_user_by_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Finds a user document from the auth_db based on a session ID."""
    user = None
    if session_id.startswith('user_'):
        user = users_collection.find_one({"clerkSessionId": session_id})
    else:
        session_record = sessions_collection.find_one({"sessionId": session_id, "verified": True})
        if session_record:
            mobile_number = session_record.get("mobileNumber")
            user = users_collection.find_one({"mobileNumber": mobile_number})
    return user

def format_message(msg: Dict[str, Any]):
    sender = msg.get('sender', 'system').upper()
    color_map = {'USER': Colors.USER, 'BOT': Colors.BOT, 'ADMIN': Colors.ADMIN}
    color = color_map.get(sender, Colors.SYSTEM)
    timestamp = msg['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
    text = msg.get('text', '')

    if text.startswith("https://i.ibb.co/"):
        return f"{color}[{timestamp}] {sender}:{Colors.RESET} {Colors.IMAGE}[IMAGE SENT]: {text}{Colors.RESET}"
    return f"{color}[{timestamp}] {sender}:{Colors.RESET} {text}"

def display_chat_history(chat_collection):
    messages = list(chat_collection.find().sort("timestamp", 1))
    if not messages:
        print(f"{Colors.SYSTEM}No chat history found for this user.{Colors.RESET}")
        return False
    for msg in messages:
        print(format_message(msg))
    print(f"{Colors.SYSTEM}--- End of History ---{Colors.RESET}\n")
    return True

def listen_for_new_messages(chat_collection, stop_event: threading.Event):
    last_message = list(chat_collection.find().sort("timestamp", -1).limit(1))
    last_timestamp = last_message[0]['timestamp'] if last_message else None
    while not stop_event.is_set():
        try:
            query = {"sender": {"$ne": "admin"}}
            if last_timestamp:
                query["timestamp"] = {"$gt": last_timestamp}
            new_messages = list(chat_collection.find(query).sort("timestamp", 1))
            if new_messages:
                for msg in new_messages:
                    with threading.Lock():
                        sys.stdout.write('\r' + ' ' * 80 + '\r') 
                        print(format_message(msg))
                        sys.stdout.write(f"{Colors.ADMIN}Admin> {Colors.RESET}")
                        sys.stdout.flush()
                    last_timestamp = msg['timestamp']
            time.sleep(1)
        except Exception as e:
            print(f"\n{Colors.SYSTEM}Error polling messages: {e}{Colors.RESET}")
            time.sleep(5)

def send_admin_message(chat_collection, text: str):
    message_doc = {"text": text, "sender": "admin", "timestamp": datetime.now(timezone.utc)}
    chat_collection.insert_one(message_doc)

# --- Main Execution ---
def main():
    try:
        session_id = sys.argv[1] if len(sys.argv) > 1 else input("Enter user's session ID: ")
        
        user = find_user_by_session(session_id)
        if not user:
            print(f"{Colors.SYSTEM}Error: No user found for session ID '{session_id}'.{Colors.RESET}")
            return

        user_id = user['_id']
        user_name = f"{user.get('firstName', 'N/A')} {user.get('lastName', '')}".strip()
        chat_collection = chats_db[f"chat_{user_id}"]

        print(f"\n{Colors.SYSTEM}Connecting to chat with {user_name} (ID: {user_id})...{Colors.RESET}")
        
        display_chat_history(chat_collection)
        print(f"{Colors.SYSTEM}Successfully connected. Type 'exit' to quit.{Colors.RESET}")

        stop_event = threading.Event()
        listener_thread = threading.Thread(target=listen_for_new_messages, args=(chat_collection, stop_event))
        listener_thread.daemon = True
        listener_thread.start()

        while True:
            admin_input = input(f"{Colors.ADMIN}Admin> {Colors.RESET}")
            if admin_input.lower() == 'exit':
                break
            if admin_input.strip():
                send_admin_message(chat_collection, admin_input)
    except (KeyboardInterrupt, EOFError):
        print(f"\n{Colors.SYSTEM}Disconnecting...{Colors.RESET}")
    finally:
        if 'stop_event' in locals():
            stop_event.set()
        print(f"{Colors.SYSTEM}Session ended.{Colors.RESET}")

if __name__ == "__main__":
    main()
