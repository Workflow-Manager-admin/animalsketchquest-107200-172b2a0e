"""
Firebase Admin SDK integration for FastAPI backend.
Handles initialization, authentication, and storage interactions using Firebase Admin.
Do NOT check secrets/configs directly into source; use environment variables.
"""

import os
import firebase_admin
from firebase_admin import credentials, auth, storage
from functools import lru_cache

from fastapi import HTTPException

# PUBLIC_INTERFACE
def get_firebase_config_from_env():
    """Fetches Firebase config from environment variables (populated externally for deployment security)."""
    config = {
        "type": "service_account",
        "project_id": os.environ.get("FIREBASE_PROJECT_ID"),
        "private_key_id": os.environ.get("FIREBASE_PRIVATE_KEY_ID"),
        "private_key": os.environ.get("FIREBASE_PRIVATE_KEY") and os.environ.get("FIREBASE_PRIVATE_KEY").replace("\\n", "\n"),
        "client_email": os.environ.get("FIREBASE_CLIENT_EMAIL"),
        "client_id": os.environ.get("FIREBASE_CLIENT_ID"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": os.environ.get("FIREBASE_CLIENT_CERT_URL"),
    }
    if not all(config.values()):
        raise RuntimeError("Missing Firebase credentials in environment variables.")
    return config

# PUBLIC_INTERFACE
@lru_cache()
def initialize_firebase():
    """Initializes the Firebase Admin SDK singleton for backend use."""
    if not firebase_admin._apps:
        config = get_firebase_config_from_env()
        cred = credentials.Certificate(config)
        firebase_admin.initialize_app(cred, {
            "projectId": os.environ.get("FIREBASE_PROJECT_ID"),
            "storageBucket": os.environ.get("FIREBASE_STORAGE_BUCKET"),
        })

# PUBLIC_INTERFACE
def verify_firebase_token(id_token: str):
    """Verifies Firebase auth token and returns decoded user info."""
    try:
        initialize_firebase()
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid Firebase token: {e}")

# PUBLIC_INTERFACE
def get_firebase_storage_bucket():
    """Returns the Firebase Storage bucket instance."""
    initialize_firebase()
    bucket_name = os.environ.get("FIREBASE_STORAGE_BUCKET")
    return storage.bucket(bucket_name)
