import requests
from requests.auth import HTTPBasicAuth
import datetime
import base64
import os
from dotenv import load_dotenv

load_dotenv()

class MpesaManager:
    """
    Orchestrates the Safaricom Daraja API integration for FYM.
    Handles OAuth (with caching), STK Push, and Callback parsing.
    """
    
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MpesaManager, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self.consumer_key = os.getenv('MPESA_CONSUMER_KEY')
        self.consumer_secret = os.getenv('MPESA_CONSUMER_SECRET')
        self.shortcode = os.getenv('MPESA_SHORTCODE')
        self.passkey = os.getenv('MPESA_PASSKEY')
        self.base_url_app = os.getenv('BASE_URL', 'https://www.findyourmatch.co.ke')
        
        # Determine environment (Sandbox vs Production)
        self.env = os.getenv('MPESA_ENV', 'sandbox').lower()
        if self.env == 'production':
            self.api_base_url = "https://api.safaricom.co.ke"
        else:
            self.api_base_url = "https://sandbox.safaricom.co.ke"
            
        # Construct Callback URL dynamically
        self.callback_url = f"{self.base_url_app.rstrip('/')}/api/v2/merchant/mpesa/callback"

        # Token Caching
        self._token = None
        self._token_expires_at = None

    def get_access_token(self):
        """
        Retrieves a valid OAuth bearer token from cache or Safaricom.
        Caches the token for 55 minutes to minimize latency.
        """
        current_time = datetime.datetime.now()

        # 1. Check if cached token is still valid
        if self._token and self._token_expires_at and current_time < self._token_expires_at:
            return self._token

        # 2. If expired or missing, fetch a new one
        api_url = f"{self.api_base_url}/oauth/v1/generate?grant_type=client_credentials"
        try:
            print("[SYSTEM] Fetching new M-Pesa OAuth token...")
            response = requests.get(
                api_url, 
                auth=HTTPBasicAuth(self.consumer_key, self.consumer_secret),
                timeout=10
            )
            response.raise_for_status()
            
            data = response.json()
            self._token = data.get('access_token')
            # Set expiration to 55 minutes (3300 seconds) to be safe (Safaricom uses 3600)
            expires_in = int(data.get('expires_in', 3599))
            buffer_time = min(expires_in - 60, 3300) # 55 mins max or 1 min before expiry
            self._token_expires_at = current_time + datetime.timedelta(seconds=buffer_time)
            
            return self._token
        except Exception as e:
            print(f"[ERROR] M-Pesa OAuth failed: {e}")
            return None

    def generate_password(self, timestamp):
        """Generates the Base64 password for STK Push."""
        data_to_encode = self.shortcode + self.passkey + timestamp
        return base64.b64encode(data_to_encode.encode()).decode('utf-8')

    def initiate_stk_push(self, phone_number, amount, account_reference, transaction_desc):
        """Initiates an M-Pesa Express (STK Push) transaction."""
        access_token = self.get_access_token()
        if not access_token:
            return {"status": "error", "message": "Failed to authenticate with M-Pesa."}

        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        password = self.generate_password(timestamp)
        
        # Clean phone number (Ensure it's in 2547XXXXXXXX format)
        phone_number = str(phone_number).strip()
        if phone_number.startswith('0'):
            phone_number = '254' + phone_number[1:]
        elif phone_number.startswith('+'):
            phone_number = phone_number[1:]
        elif not phone_number.startswith('254') and len(phone_number) == 9:
            phone_number = '254' + phone_number

        api_url = f"{self.api_base_url}/mpesa/stkpush/v1/processrequest"
        headers = {"Authorization": f"Bearer {access_token}"}
        
        payload = {
            "BusinessShortCode": self.shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": int(amount),
            "PartyA": phone_number,
            "PartyB": self.shortcode,
            "PhoneNumber": phone_number,
            "CallBackURL": self.callback_url,
            "AccountReference": account_reference,
            "TransactionDesc": transaction_desc
        }

        try:
            print(f"[SYSTEM] Initiating STK Push to {phone_number} (Env: {self.env})")
            response = requests.post(api_url, json=payload, headers=headers, timeout=15)
            
            if response.status_code != 200:
                print(f"[ERROR] M-Pesa API Response: {response.text}")
            
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"status": "error", "message": f"M-Pesa API Connectivity Issue: {str(e)}"}
        except Exception as e:
            return {"status": "error", "message": f"STK Push Failed: {str(e)}"}
