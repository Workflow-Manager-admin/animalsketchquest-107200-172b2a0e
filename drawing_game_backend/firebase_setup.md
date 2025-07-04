# Firebase Integration Setup for FastAPI Backend

## Overview
This backend uses Firebase Admin SDK for authentication and storage (drawing uploads, user management, etc.). Credentials are sourced securely from environment variables.

## Configuration Steps

1. **Create a Firebase Service Account:**
   - In [Firebase Console](https://console.firebase.google.com/), navigate to Project Settings > Service Accounts.
   - Generate a new private key and download the JSON.

2. **Populate Environment Variables** using details from the service account JSON:
   - FIREBASE_PROJECT_ID
   - FIREBASE_PRIVATE_KEY_ID
   - FIREBASE_PRIVATE_KEY (escape newlines as `\\n` in env)
   - FIREBASE_CLIENT_EMAIL
   - FIREBASE_CLIENT_ID
   - FIREBASE_CLIENT_CERT_URL
   - FIREBASE_STORAGE_BUCKET (use `doodlefinder.firebasestorage.app` per provided config)

   Example `.env`:
   ```
   FIREBASE_PROJECT_ID=doodlefinder
   FIREBASE_PRIVATE_KEY_ID=xxx
   FIREBASE_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----\\nABC...\\n-----END PRIVATE KEY-----\\n
   FIREBASE_CLIENT_EMAIL=firebase-adminsdk-abc@doodlefinder.iam.gserviceaccount.com
   FIREBASE_CLIENT_ID=306458973633
   FIREBASE_CLIENT_CERT_URL=https://www.googleapis.com/robot/v1/metadata/x509/...<rest>
   FIREBASE_STORAGE_BUCKET=doodlefinder.firebasestorage.app
   ```

3. **Usage Example in FastAPI**:
   ```python
   from .firebase_admin_client import verify_firebase_token, get_firebase_storage_bucket

   # Verify an incoming Firebase user ID token
   user = verify_firebase_token(id_token)

   # Interact with Firebase storage
   bucket = get_firebase_storage_bucket()
   blob = bucket.blob("some/path/to/image.png")
   blob.upload_from_filename("local_image.png")
   ```

## Notes
- Do not commit secrets or any `firebaseConfig` directly into the codebase.
- All Firebase operations (storage, user auth verification) are available via provided helpers; see `src/api/firebase_admin_client.py`.
- This backend will expect correctly set environment variables to function.
