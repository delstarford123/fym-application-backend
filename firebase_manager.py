import threading
import os
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, db, storage

load_dotenv()

class FirebaseManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        # Double-checked locking pattern for thread safety
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(FirebaseManager, cls).__new__(cls)
                    cls._instance._initialize_firebase()
        return cls._instance

    def _initialize_firebase(self):
        """
        Initializes the Firebase Admin SDK with the FYM buckets.
        Verifies if an app already exists to prevent runtime exceptions.
        """
        if not firebase_admin._apps:
            # Load the service account key (ensure this file is secured and ignored in version control)
            try:
                # Load configuration from environment variables
                cred_path = os.getenv('FIREBASE_SERVICE_ACCOUNT_KEY', 'serviceAccountKey.json')
                db_url = os.getenv('FIREBASE_DB_URL', 'https://mmust-dating-site-default-rtdb.firebaseio.com/')
                storage_bucket = os.getenv('FIREBASE_STORAGE_BUCKET', 'mmust-dating-site.firebasestorage.app')
                
                cred = credentials.Certificate(cred_path)
                
                # Initialize with specific FYM infrastructure buckets
                firebase_admin.initialize_app(cred, {
                    'databaseURL': db_url,
                    'storageBucket': storage_bucket
                })
                print("[SYSTEM] Firebase Admin SDK initialized successfully.")
            except Exception as e:
                print(f"[ERROR] Failed to initialize Firebase Admin SDK: {e}")
                # Re-raise to prevent the app from starting in a broken state
                raise
        else:
            print("[SYSTEM] Firebase Admin SDK already running. Connecting to existing instance.")

    def get_db_reference(self, path):
        """Returns a reference to the Realtime Database at the specified path."""
        return db.reference(path)

    def get_storage_bucket(self):
        """Returns a reference to the default FYM storage bucket."""
        return storage.bucket()
