# backend/src/auth.py

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import os
import requests
from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime
from typing import Optional

load_dotenv()

API_KEY = os.getenv("TWO_FACTOR_API_KEY")
MONGO_URI = os.getenv("MONGO_URI")

# Connect to MongoDB
client = MongoClient(MONGO_URI)
db = client.mobileauth  # database
sessions_collection = db.sessions  # collection for sessionId storage
users_collection = db.users  # collection for user details

router = APIRouter()

class OTPRequest(BaseModel):
    mobileNumber: str

class OTPVerifyRequest(BaseModel):
    sessionId: str
    otp: str

class ValidateSessionRequest(BaseModel):
    sessionId: str

class UserDetailsRequest(BaseModel):
    sessionId: str
    mobileNumber: str
    firstName: str
    lastName: Optional[str] = None
    email: Optional[str] = None

# Add the Google user details model
class GoogleUserDetailsRequest(BaseModel):
    clerkSessionId: str  # Use Clerk's session ID
    email: str
    firstName: str
    lastName: Optional[str] = None

@router.post("/send-otp")
def send_otp(request: OTPRequest):
    url = f"https://2factor.in/API/V1/{API_KEY}/SMS/{request.mobileNumber}/AUTOGEN3"
    resp = requests.get(url)

    try:
        data = resp.json()
    except Exception:
        raise HTTPException(status_code=500, detail="Invalid response from 2factor.in")

    if data.get("Status") != "Success":
        raise HTTPException(status_code=400, detail=data.get("Details", "Failed to send OTP"))

    session_id = data["Details"]

    # Store sessionId with mobileNumber in MongoDB (upsert)
    sessions_collection.update_one(
        {"mobileNumber": request.mobileNumber},
        {
            "$set": {
                "sessionId": session_id, 
                "createdAt": data.get("CreatedOn"),
                "verified": False,
                "updatedAt": datetime.utcnow()
            }
        },
        upsert=True,
    )

    return {"sessionId": session_id}

@router.post("/verify-otp")
def verify_otp(request: OTPVerifyRequest):
    # Check sessionId matches session stored in DB
    record = sessions_collection.find_one({"sessionId": request.sessionId})

    if not record:
        raise HTTPException(status_code=400, detail="Invalid or expired sessionId")

    url = f"https://2factor.in/API/V1/{API_KEY}/SMS/VERIFY/{request.sessionId}/{request.otp}"
    resp = requests.get(url)

    try:
        data = resp.json()
    except Exception:
        raise HTTPException(status_code=500, detail="Invalid response from 2factor.in")

    if data.get("Status") != "Success":
        raise HTTPException(status_code=400, detail=data.get("Details", "OTP verification failed"))

    # Mark session verified, add timestamp, etc.
    sessions_collection.update_one(
        {"sessionId": request.sessionId},
        {
            "$set": {
                "verified": True, 
                "verifiedAt": data.get("VerifiedOn"),
                "updatedAt": datetime.utcnow()
            }
        },
    )

    # Check if user details already exist for this mobile number
    mobile_number = record.get("mobileNumber")
    user_exists = users_collection.find_one({"mobileNumber": mobile_number}) is not None

    return {"success": True, "userExists": user_exists}

@router.post("/validate-session")
def validate_session(request: ValidateSessionRequest):
    # Check if session exists and is verified
    session_doc = sessions_collection.find_one({
        "sessionId": request.sessionId, 
        "verified": True
    })
    
    if not session_doc:
        return {"success": False}
    
    # Check if user details exist for this session's mobile number
    mobile_number = session_doc.get("mobileNumber")
    user_doc = users_collection.find_one({"mobileNumber": mobile_number})
    
    if user_doc:
        return {"success": True}
    
    return {"success": False}

@router.post("/save-user-details")
def save_user_details(request: UserDetailsRequest):
    # Validate session first
    session_record = sessions_collection.find_one({
        "sessionId": request.sessionId,
        "verified": True
    })

    if not session_record:
        raise HTTPException(status_code=400, detail="Invalid or unverified session")

    # Verify mobile number matches
    if session_record.get("mobileNumber") != request.mobileNumber:
        raise HTTPException(status_code=400, detail="Mobile number mismatch")

    # Check if user already exists
    existing_user = users_collection.find_one({"mobileNumber": request.mobileNumber})

    user_data = {
        "mobileNumber": request.mobileNumber,
        "firstName": request.firstName,
        "lastName": request.lastName,
        "email": request.email,
        "sessionId": request.sessionId,
        "updatedAt": datetime.utcnow()
    }

    if existing_user:
        # Update existing user
        users_collection.update_one(
            {"mobileNumber": request.mobileNumber},
            {"$set": user_data}
        )
    else:
        # Create new user
        user_data["createdAt"] = datetime.utcnow()
        users_collection.insert_one(user_data)

    # Update session with user completion flag
    sessions_collection.update_one(
        {"sessionId": request.sessionId},
        {
            "$set": {
                "userDetailsCompleted": True,
                "updatedAt": datetime.utcnow()
            }
        }
    )

    return {"success": True}

@router.get("/user-profile/{session_id}")
def get_user_profile(session_id: str):
    # Validate session
    session_record = sessions_collection.find_one({
        "sessionId": session_id,
        "verified": True
    })

    if not session_record:
        raise HTTPException(status_code=400, detail="Invalid or unverified session")

    # Get user details
    mobile_number = session_record.get("mobileNumber")
    user_record = users_collection.find_one(
        {"mobileNumber": mobile_number},
        {"_id": 0, "sessionId": 0}  # Exclude sensitive fields
    )

    if not user_record:
        raise HTTPException(status_code=404, detail="User profile not found")

    return user_record

# Google OAuth endpoint
@router.post("/save-google-user-details")
def save_google_user_details(request: GoogleUserDetailsRequest):
    """
    Saves user details obtained from Google Sign-In via Clerk.
    Uses email as the primary identifier.
    """
    try:
        # --- Basic Validation ---
        if not request.clerkSessionId:
            raise HTTPException(status_code=400, detail="Clerk session ID is required")
        
        if not request.email:
            raise HTTPException(status_code=400, detail="Email is required")
        
        if not request.firstName:
            raise HTTPException(status_code=400, detail="First name is required")

        print(f"Processing Google user: {request.email}")

        # --- User Data Handling ---
        # Use email as the unique identifier for Google users
        existing_user = users_collection.find_one({"email": request.email})

        # Prepare user data to save/update
        user_data_to_set = {
            "email": request.email,
            "firstName": request.firstName,
            "lastName": request.lastName,
            "clerkSessionId": request.clerkSessionId,
            "authProvider": "google",  # Track auth method
            "updatedAt": datetime.utcnow()
        }
        
        user_data_to_set_on_insert = {
            "createdAt": datetime.utcnow(),
            "mobileNumber": None  # Explicitly set to None for Google users
        }

        if existing_user:
            # --- Update Existing User ---
            result = users_collection.update_one(
                {"email": request.email},
                {"$set": user_data_to_set}
            )
            
            if result.matched_count == 0:
                raise HTTPException(status_code=500, detail="Failed to update user record")
                
            print(f"Updated existing user: {request.email}")

        else:
            # --- Create New User ---
            new_user_data = {**user_data_to_set, **user_data_to_set_on_insert}
            result = users_collection.insert_one(new_user_data)
            
            if not result.inserted_id:
                raise HTTPException(status_code=500, detail="Failed to create user record")
                
            print(f"Created new user: {request.email}")

        return {
            "success": True, 
            "message": "User details saved successfully",
            "userExists": existing_user is not None
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        print(f"Error saving Google user details: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# Get user profile by Clerk session ID
@router.get("/user-profile-clerk/{clerk_session_id}")
def get_user_profile_by_clerk_session(clerk_session_id: str):
    """
    Get user profile using Clerk session ID (for Google users)
    """
    try:
        # Find user by Clerk session ID
        user_record = users_collection.find_one(
            {"clerkSessionId": clerk_session_id},
            {"_id": 0}  # Exclude MongoDB _id field
        )

        if not user_record:
            raise HTTPException(status_code=404, detail="User profile not found")

        return user_record
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error getting user profile by Clerk session: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
class AddMobileToGoogleUserRequest(BaseModel):
    clerkSessionId: str
    mobileNumber: str

@router.post("/add-mobile-to-google-user")
def add_mobile_to_google_user(request: AddMobileToGoogleUserRequest):
    """
    Adds mobile number to an existing Google user account.
    """
    try:
        # Validate inputs
        if not request.clerkSessionId:
            raise HTTPException(status_code=400, detail="Clerk session ID is required")
        
        if not request.mobileNumber:
            raise HTTPException(status_code=400, detail="Mobile number is required")
        
        if len(request.mobileNumber) != 10:
            raise HTTPException(status_code=400, detail="Mobile number must be 10 digits")

        print(f"Adding mobile number to Google user with session: {request.clerkSessionId}")

        # Find the user by Clerk session ID
        existing_user = users_collection.find_one({"clerkSessionId": request.clerkSessionId})

        if not existing_user:
            raise HTTPException(status_code=404, detail="User not found")

        # Update user with mobile number
        result = users_collection.update_one(
            {"clerkSessionId": request.clerkSessionId},
            {
                "$set": {
                    "mobileNumber": request.mobileNumber,
                    "updatedAt": datetime.utcnow()
                }
            }
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=500, detail="Failed to update user record")
            
        print(f"Successfully added mobile number for user: {existing_user.get('email')}")

        return {
            "success": True, 
            "message": "Mobile number added successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error adding mobile number to Google user: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
