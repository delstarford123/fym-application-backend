from flask import Blueprint, request, jsonify
from functools import wraps
import time
import jwt
import uuid
import random
from werkzeug.utils import secure_filename
from firebase_manager import FirebaseManager
from jwt_manager import generate_jwt, decode_jwt
from institution_validator import InstitutionValidator
from security_utils import hash_password, verify_password
from mpesa_manager import MpesaManager
from email_manager import EmailManager
from voice_proxy import VoiceProxy
import json

# Initialize the V2 specific blueprint
v2_blueprint = Blueprint('api_v2', __name__, url_prefix='/api/v2')

# Initialize Managers
firebase = FirebaseManager()
mpesa = MpesaManager()
voice_proxy = VoiceProxy()

# Tracking dictionary. For distributed server deployments using Gevent, 
# this state should ideally be synchronized via Firebase RTDB.
_failed_attempts = {}

MAX_FAILURES = 5
LOCKOUT_WINDOW = 900  # 15 minutes in seconds

# Explicit database access constraints
ADMIN_EMAIL_WHITELIST = {"primary.admin@example.com", "secondary.admin@example.com"}

# Allowed extensions for high-resolution images
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}

def allowed_file(filename: str) -> bool:
    """Validates the file extension against approved formats."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def token_required(f):
    """
    Middleware decorator to enforce JWT validation on protected V2 endpoints.
    Automatically handles missing, invalid, or expired tokens.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = None
        
        # Extract the token from the standard Authorization header
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            parts = auth_header.split()
            if len(parts) == 2 and parts[0] == 'Bearer':
                token = parts[1]

        if not token:
            return jsonify({'status': 'error', 'message': 'Authentication token is missing.'}), 401

        try:
            # Decode the token. PyJWT automatically verifies the 'exp' timestamp.
            decoded_data = decode_jwt(token)
            current_user_id = decoded_data['sub']
            
        except jwt.ExpiredSignatureError:
            return jsonify({'status': 'error', 'message': 'Session expired. Please log in again.'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'status': 'error', 'message': 'Invalid authentication token.'}), 401

        # Pass the decoded user ID into the protected route
        return f(current_user_id, *args, **kwargs)

    return decorated_function

def enforce_login_security(f):
    """
    Middleware decorator to intercept login requests, track failed attempts,
    and enforce account locking protocols to mitigate brute-force attacks.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        payload = request.get_json(silent=True) or {}
        identifier = payload.get('email') or request.remote_addr

        # Retrieve current failure record
        record = _failed_attempts.get(identifier, {'count': 0, 'locked_until': 0})
        current_time = time.time()

        # Check if the account is currently locked
        if record['count'] >= MAX_FAILURES:
            if current_time < record['locked_until']:
                remaining_time = int((record['locked_until'] - current_time) / 60)
                return jsonify({
                    "status": "error", 
                    "message": f"Account locked due to excessive failed attempts. Try again in {remaining_time} minutes."
                }), 429
            else:
                # Lockout window expired; reset the counter
                record = {'count': 0, 'locked_until': 0}
                _failed_attempts[identifier] = record

        # Execute the actual login function
        response_data, status_code = f(*args, **kwargs)

        # Handle different return types from the route function
        if isinstance(response_data, tuple):
            actual_response = response_data[0]
        else:
            actual_response = response_data

        # Post-execution validation: Check if login failed (assuming 401 Unauthorized for failure)
        if status_code == 401:
            record['count'] += 1
            if record['count'] >= MAX_FAILURES:
                record['locked_until'] = current_time + LOCKOUT_WINDOW
            _failed_attempts[identifier] = record
        elif status_code == 200:
            # On successful login, clear any failure history
            if identifier in _failed_attempts:
                del _failed_attempts[identifier]

        return actual_response, status_code

    return decorated_function

@v2_blueprint.route('/chat/ws')
def chat_websocket():
    """
    WebSocket endpoint for real-time chat and voice synthesis.
    """
    ws = request.environ.get('wsgi.websocket')
    if not ws:
        return "Upgrade Required", 426

    print("[SOCKET] New connection established.")
    
    try:
        while True:
            message = ws.receive()
            if message is None:
                break
            
            data = json.loads(message)
            msg_type = data.get('type')

            if msg_type == 'text_message':
                # Broadcast or handle standard text
                print(f"[SOCKET] Text received: {data.get('content')}")
                ws.send(json.dumps({
                    "type": "text_message",
                    "sender": "System",
                    "content": f"Echo: {data.get('content')}"
                }))

            elif msg_type == 'voice_request':
                # Synthesize text and stream bytes
                text = data.get('content', '')
                voice_engine.stream_voice_note(ws, text)

    except Exception as e:
        print(f"[SOCKET] Connection error: {e}")
    finally:
        print("[SOCKET] Connection closed.")
        if not ws.closed:
            ws.close()
    return ""

@v2_blueprint.route('/merchant/mpesa/stkpush', methods=['POST'])
@token_required
def mpesa_stk_push(current_user_id):
    """
    Triggers an M-Pesa STK Push for merchant subscriptions or booking fees.
    """
    payload = request.get_json(silent=True) or {}
    phone_number = payload.get('phone_number')
    amount = payload.get('amount')
    account_ref = payload.get('account_ref', 'FYMV2_PAYMENT')
    description = payload.get('description', 'FYM Service Payment')

    if not phone_number or not amount:
        return jsonify({"status": "error", "message": "Phone number and amount are required."}), 400

    # PROMO BYPASS: Grant instant access for the specified testing/promo number
    # Using endswith to handle all formats (07..., 254..., +254...)
    clean_phone = str(phone_number).strip().replace('+', '')
    
    print(f"[AUTH] Payment initiation check for: {clean_phone}")

    if clean_phone.endswith('707605751'):
        print(f"[PROMO] Protocol Match! Granting instant access for user: {current_user_id}")
        firebase.get_db_reference(f'/users/{current_user_id}').update({
            'is_paid': True,
            'subscription_type': 'promo_free_bypass',
            'expiry_date': time.time() + (30 * 24 * 3600) # 1 month access
        })
        return jsonify({
            "status": "success", 
            "message": "Protocol accepted. Instant network access granted!", 
            "checkout_id": "PROMO_BYPASS_SUCCESS"
        }), 200

    # 1. Initiate STK Push
    result = mpesa.initiate_stk_push(phone_number, amount, account_ref, description)

    if result.get('ResponseCode') == '0':
        # 2. Log the pending transaction in Firebase for tracking
        checkout_id = result.get('CheckoutRequestID')
        firebase.get_db_reference(f'/transactions/pending/{checkout_id}').set({
            'user_id': current_user_id,
            'phone': phone_number,
            'amount': amount,
            'status': 'pending',
            'created_at': time.time()
        })
        return jsonify({"status": "success", "message": "STK Push initiated. Check your phone.", "checkout_id": checkout_id}), 200
    else:
        return jsonify({"status": "error", "message": result.get('CustomerMessage', 'Transaction failed to initiate.')}), 500

@v2_blueprint.route('/merchant/mpesa/callback', methods=['POST'])
def mpesa_callback():
    """
    Async webhook called by Safaricom once the user completes/cancels the STK Push.
    """
    data = request.get_json()
    
    try:
        callback_data = data['Body']['stkCallback']
        result_code = callback_data['ResultCode']
        checkout_id = callback_data['CheckoutRequestID']

        # Retrieve the original pending transaction
        tx_ref = firebase.get_db_reference(f'/transactions/pending/{checkout_id}')
        tx_data = tx_ref.get()

        if result_code == 0:
            # Payment Successful
            items = callback_data['CallbackMetadata']['Item']
            receipt = next((item['Value'] for item in items if item['Name'] == 'MpesaReceiptNumber'), None)
            amount = next((item['Value'] for item in items if item['Name'] == 'Amount'), None)
            
            if tx_data:
                user_id = tx_data['user_id']
                
                # 2. Record successful payment
                firebase.get_db_reference(f'/transactions/success/{receipt}').set({
                    **tx_data,
                    'receipt': receipt,
                    'amount_paid': amount,
                    'completed_at': time.time(),
                    'status': 'completed'
                })
                
                # 3. Update User/Merchant status
                firebase.get_db_reference(f'/users/{user_id}').update({
                    'is_paid': True,
                    'last_payment_receipt': receipt
                })

                # 4. Cleanup pending record
                tx_ref.delete()
        else:
            # Payment Failed/Cancelled
            if tx_data:
                firebase.get_db_reference(f'/transactions/failed/{checkout_id}').set({
                    **tx_data,
                    'reason': callback_data.get('ResultDesc', 'Unknown failure'),
                    'failed_at': time.time()
                })
                tx_ref.delete()

        return jsonify({"ResultCode": 0, "ResultDesc": "Success"}), 200

    except Exception as e:
        print(f"[ERROR] Callback processing error: {e}")
        return jsonify({"ResultCode": 1, "ResultDesc": "Internal Server Error"}), 500

@v2_blueprint.route('/merchant/upload-venue', methods=['POST'])
@token_required
def upload_venue_image(current_user_id):
    """
    Secure endpoint for B2B merchants to upload high-resolution venue images.
    Files are streamed directly to Firebase Storage.
    """
    # 1. Validate the request structure
    if 'image' not in request.files:
        return jsonify({"status": "error", "message": "No image file provided."}), 400
        
    file = request.files['image']
    
    if file.filename == '':
        return jsonify({"status": "error", "message": "Empty file payload."}), 400
        
    if not allowed_file(file.filename):
        return jsonify({"status": "error", "message": "Unsupported file format."}), 415

    try:
        # 2. Sanitize and construct the unique storage path
        safe_filename = secure_filename(file.filename)
        unique_id = str(uuid.uuid4())
        extension = safe_filename.rsplit('.', 1)[1].lower()
        storage_path = f"merchants/{current_user_id}/venues/venue_{unique_id}.{extension}"

        # 3. Stream to Firebase Storage Bucket
        bucket = firebase.get_storage_bucket()
        blob = bucket.blob(storage_path)
        
        # Upload directly from the memory buffer
        blob.upload_from_file(file, content_type=file.content_type)
        
        # 4. Make the file publicly accessible and retrieve the URL
        blob.make_public()
        public_url = blob.public_url

        # 5. Update the Merchant's Realtime Database Record
        db_ref = firebase.get_db_reference(f'/users/{current_user_id}/venues')
        db_ref.push({
            'image_url': public_url,
            'uploaded_at': {".sv": "timestamp"}, # Firebase server timestamp
            'status': 'active'
        })

        return jsonify({
            "status": "success",
            "message": "Venue image uploaded and mapped successfully.",
            "url": public_url
        }), 201

    except Exception as e:
        return jsonify({"status": "error", "message": f"Storage transaction failed: {str(e)}"}), 500

@v2_blueprint.route('/system/status', methods=['GET'])
def system_status():
    """Verify Firebase connection state."""
    try:
        # Test database connection
        ref = firebase.get_db_reference('/system_status')
        ref.set({'status': 'operational', 'version': '2.0', 'timestamp': time.time()})
        
        return jsonify({"status": "success", "message": "FYM Backend Operational"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@v2_blueprint.route('/institutions', methods=['GET'])
def get_institutions():
    """Fetches the comprehensive list of Kenyan universities and colleges."""
    institutions = InstitutionValidator.get_all_institutions()
    return jsonify({
        "status": "success",
        "count": len(institutions),
        "data": institutions
    }), 200

@v2_blueprint.route('/auth/send_otp', methods=['POST'])
def send_otp():
    """Generates and sends a 6-digit OTP to the provided student email."""
    payload = request.get_json(silent=True) or {}
    email = payload.get('email', '').lower().strip()
    institution_id = payload.get('institution_id')
    reg_number = payload.get('reg_number')

    if not all([email, institution_id, reg_number]):
        return jsonify({"status": "error", "message": "Missing verification parameters."}), 400

    # 1. Validate the Reg Number format first
    if not InstitutionValidator.validate_registration_number(institution_id, reg_number):
        return jsonify({"status": "error", "message": "Invalid student ID format."}), 400

    # 2. Generate 6-digit code
    otp_code = str(random.randint(100000, 999999))
    
    # 3. Store in Firebase with timestamp
    safe_email = email.replace('.', ',')
    firebase.get_db_reference(f'/otp_codes/{safe_email}').set({
        'code': otp_code,
        'expires_at': time.time() + 600 # 10 minutes
    })

    # 4. Send via Email Gateway
    success = EmailManager.send_otp(email, otp_code)
    
    if success:
        return jsonify({"status": "success", "message": "Verification code sent to your email."}), 200
    else:
        return jsonify({"status": "error", "message": "Failed to deliver email. Check your address."}), 500

@v2_blueprint.route('/auth/verify_otp', methods=['POST'])
def verify_otp():
    """Validates the 6-digit code provided by the user."""
    payload = request.get_json(silent=True) or {}
    email = payload.get('email', '').lower().strip()
    provided_code = payload.get('code')

    if not email or not provided_code:
        return jsonify({"status": "error", "message": "Missing email or code."}), 400

    safe_email = email.replace('.', ',')
    otp_ref = firebase.get_db_reference(f'/otp_codes/{safe_email}')
    otp_data = otp_ref.get()

    if not otp_data:
        return jsonify({"status": "error", "message": "No active verification found. Request a new code."}), 404

    if time.time() > otp_data['expires_at']:
        otp_ref.delete()
        return jsonify({"status": "error", "message": "Verification code expired."}), 400

    if str(provided_code) == str(otp_data['code']):
        # Mark as verified (optional: use a flag for registration step)
        otp_ref.delete()
        return jsonify({"status": "success", "message": "Identity verified successfully."}), 200
    else:
        return jsonify({"status": "error", "message": "Incorrect verification code."}), 400

@v2_blueprint.route('/auth/register', methods=['POST'])
def user_register():
    """
    Registers a new user (Student or Merchant) with role-specific validation.
    """
    payload = request.get_json(silent=True) or {}
    email = payload.get('email', '').lower().strip()
    raw_password = payload.get('password')
    phone = payload.get('phone')
    role = payload.get('role', 'student') # 'student' or 'merchant'

    print(f"[AUTH] Registration attempt: {email}")

    if not all([email, raw_password, phone]):
        return jsonify({"status": "error", "message": "Missing required fields."}), 400

    safe_email_key = email.replace('.', ',') 
    users_ref = firebase.get_db_reference(f'/users/{safe_email_key}')
    if users_ref.get():
        print(f"[AUTH] Registration failed: {email} already exists.")
        return jsonify({"status": "error", "message": "User already exists."}), 409

    user_data = {
        'email': email,
        'full_name': payload.get('full_name'),
        'password_hash': hash_password(raw_password),
        'phone': phone,
        'role': role,
        'status': 'active',
        'is_online': True,
        'is_paid': False, # Default to unpaid
        'age': payload.get('age'),
        'gender': payload.get('gender', 'not_specified'), # Added gender for matching logic
        'interests': payload.get('interests', []),
        'intent': payload.get('intent', 'friendship'), # 'friendship' or 'relationship'
        'created_at': time.time(),
        'profile_photo': None
    }

    if role == 'student':
        institution_id = payload.get('institution_id')
        reg_number = payload.get('reg_number')
        admission_year = payload.get('admission_year')
        program_type = payload.get('program_type', 'general')

        if not all([institution_id, reg_number, admission_year]):
            return jsonify({"status": "error", "message": "Missing student details."}), 400

        if not InstitutionValidator.validate_registration_number(institution_id, reg_number):
            return jsonify({"status": "error", "message": "Invalid student ID format."}), 400

        user_data.update({
            'institution': institution_id.lower(),
            'registration_number': reg_number.upper(),
            'graduation_year': InstitutionValidator.calculate_graduation_date(int(admission_year), program_type),
            'verification_status': 'pending'
        })
    elif role == 'merchant':
        business_name = payload.get('business_name')
        if not business_name:
            return jsonify({"status": "error", "message": "Business name required for merchants."}), 400
        
        user_data.update({
            'business_name': business_name,
            'is_verified_merchant': False
        })

    users_ref.set(user_data)
    return jsonify({"status": "success", "message": "Account created successfully."}), 201

@v2_blueprint.route('/users/discover', methods=['GET'])
@token_required
def discover_users(current_user_id):
    """Returns students with AI-powered compatibility scoring."""
    try:
        # 1. Fetch current user data for context
        current_user = firebase.get_db_reference(f'/users/{current_user_id}').get()
        if not current_user:
            return jsonify({"status": "error", "message": "Current user not found."}), 404

        # 2. Fetch all students
        users_ref = firebase.get_db_reference('/users')
        query = users_ref.order_by_child('role').equal_to('student').limit_to_first(100)
        all_students = query.get() or {}
        
        discovery_list = []
        user_age = current_user.get('age', 21)
        user_interests = set(current_user.get('interests', []))
        user_gender = current_user.get('gender', 'other')

        for uid, data in all_students.items():
            if uid == current_user_id:
                continue
            
            # AI Compatibility Logic
            target_age = data.get('age', 21)
            target_interests = set(data.get('interests', []))
            target_gender = data.get('gender', 'other')
            
            score = 50 # Base score
            
            # Interest Overlap (Hobbies)
            common = user_interests.intersection(target_interests)
            score += len(common) * 10
            
            # Strategic Age Bracket Logic (+/- 3 years)
            age_diff = user_age - target_age
            if abs(age_diff) <= 3:
                score += 20
                # Specific "Male higher, Female lower" synergy bonus
                if user_gender == 'male' and target_gender == 'female' and age_diff >= 0:
                    score += 15
                elif user_gender == 'female' and target_gender == 'male' and age_diff <= 0:
                    score += 15

            # Cap score at 99%
            final_score = min(score, 99)

            discovery_list.append({
                'id': uid,
                'full_name': data.get('full_name'),
                'institution': data.get('institution'),
                'is_online': data.get('is_online', False),
                'profile_photo': data.get('profile_photo'),
                'age': target_age,
                'intent': data.get('intent'),
                'interests': data.get('interests', []),
                'compatibility': final_score
            })
            
        # Sort by compatibility
        discovery_list.sort(key=lambda x: x['compatibility'], reverse=True)
            
        return jsonify({"status": "success", "data": discovery_list}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@v2_blueprint.route('/users/like', methods=['POST'])
@token_required
def like_user(current_user_id):
    """Marks interest and triggers instant mutual matching."""
    payload = request.get_json(silent=True) or {}
    target_id = payload.get('target_id')
    
    if not target_id:
        return jsonify({"status": "error", "message": "Target ID required."}), 400

    try:
        # INSTANT MATCH PROTOCOL: In this AI network, a like results in an automatic connection
        match_id = f"match_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        
        # 1. Log the Match
        match_data = {
            'match_id': match_id,
            'users': [current_user_id, target_id],
            'timestamp': time.time(),
            'status': 'active'
        }
        
        firebase.get_db_reference(f'/matches/{match_id}').set(match_data)
        
        # 2. Update both users' match lists
        firebase.get_db_reference(f'/users/{current_user_id}/matches/{target_id}').set(match_id)
        firebase.get_db_reference(f'/users/{target_id}/matches/{current_user_id}').set(match_id)
        
        return jsonify({
            "status": "success", 
            "message": "Instant match established! Network connection live.",
            "match_id": match_id
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@v2_blueprint.route('/user/update-profile', methods=['POST'])
@token_required
def update_profile(current_user_id):
    """Updates the current user's profile details."""
    payload = request.get_json(silent=True) or {}
    
    # List of allowed fields to update
    updatable_fields = ['full_name', 'age', 'interests', 'intent', 'phone', 'is_online', 'gender']
    update_data = {k: v for k, v in payload.items() if k in updatable_fields}
    
    if not update_data:
        return jsonify({"status": "error", "message": "No valid fields provided for update."}), 400

    try:
        firebase.get_db_reference(f'/users/{current_user_id}').update(update_data)
        return jsonify({"status": "success", "message": "Profile updated successfully."}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@v2_blueprint.route('/user/upload-profile-photo', methods=['POST'])
@token_required
def upload_profile_photo(current_user_id):
    """Updates the user's profile photo."""
    if 'image' not in request.files:
        return jsonify({"status": "error", "message": "No file provided."}), 400
    
    file = request.files['image']
    if not allowed_file(file.filename):
        return jsonify({"status": "error", "message": "Invalid file type."}), 415

    try:
        ext = secure_filename(file.filename).rsplit('.', 1)[1].lower()
        path = f"profiles/{current_user_id}/photo_{int(time.time())}.{ext}"
        bucket = firebase.get_storage_bucket()
        blob = bucket.blob(path)
        blob.upload_from_file(file, content_type=file.content_type)
        blob.make_public()
        
        firebase.get_db_reference(f'/users/{current_user_id}').update({'profile_photo': blob.public_url})
        return jsonify({"status": "success", "url": blob.public_url}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@v2_blueprint.route('/merchant/upload-media', methods=['POST'])
@token_required
def upload_merchant_media(current_user_id):
    """Allows merchants to upload images, videos, or audio for their venue."""
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file provided."}), 400
    
    file = request.files['file']
    media_type = request.form.get('type', 'image') # 'image', 'video', 'audio'
    
    try:
        safe_name = secure_filename(file.filename)
        path = f"merchants/{current_user_id}/{media_type}s/{int(time.time())}_{safe_name}"
        bucket = firebase.get_storage_bucket()
        blob = bucket.blob(path)
        blob.upload_from_file(file, content_type=file.content_type)
        blob.make_public()
        
        db_ref = firebase.get_db_reference(f'/users/{current_user_id}/media')
        db_ref.push({
            'url': blob.public_url,
            'type': media_type,
            'uploaded_at': time.time()
        })
        return jsonify({"status": "success", "url": blob.public_url}), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@v2_blueprint.route('/chat/share-file', methods=['POST'])
@token_required
def chat_share_file(current_user_id):
    """Endpoint for sharing files/media within a chat session."""
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file provided."}), 400
    
    file = request.files['file']
    chat_id = request.form.get('chat_id')
    
    if not chat_id:
        return jsonify({"status": "error", "message": "Chat ID required."}), 400

    try:
        safe_name = secure_filename(file.filename)
        path = f"chats/{chat_id}/{int(time.time())}_{safe_name}"
        bucket = firebase.get_storage_bucket()
        blob = bucket.blob(path)
        blob.upload_from_file(file, content_type=file.content_type)
        blob.make_public()
        
        return jsonify({"status": "success", "url": blob.public_url, "name": safe_name}), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@v2_blueprint.route('/auth/login', methods=['POST'])
@enforce_login_security
def user_login():
    """Authenticates the user and returns a JWT."""
    payload = request.get_json(silent=True) or {}
    email = payload.get('email', '').lower().strip()
    provided_password = payload.get('password')

    print(f"[AUTH] Login attempt: {email}")

    if not email or not provided_password:
        return jsonify({"status": "error", "message": "Missing email or password"}), 400

    # Retrieve stored user data from Firebase
    safe_email_key = email.replace('.', ',')
    user_ref = firebase.get_db_reference(f'/users/{safe_email_key}')
    user_data = user_ref.get()

    if not user_data:
        print(f"[AUTH] Login failed: User {email} not found.")
        return jsonify({"status": "error", "message": "Invalid credentials"}), 401

    if verify_password(user_data.get('password_hash'), provided_password):
        print(f"[AUTH] Login successful: {email}")
        # Mark user as online
        user_ref.update({'is_online': True})
        
        # Generate the secure token
        token = generate_jwt(safe_email_key)
        
        return jsonify({
            "status": "success", 
            "message": "Authentication successful.",
            "token": token
        }), 200
    
    print(f"[AUTH] Login failed: Incorrect password for {email}.")
    return jsonify({"status": "error", "message": "Invalid credentials"}), 401

@v2_blueprint.route('/user/profile', methods=['GET'])
@token_required
def get_user_profile(current_user_id):
    """A strictly protected endpoint requiring a valid JWT."""
    try:
        # Fetch the user's data from Firebase using current_user_id
        # Note: current_user_id is the safe_email_key (e.g., test@example,com)
        user_record = firebase.get_db_reference(f'/users/{current_user_id}').get()
        
        if not user_record:
            return jsonify({"status": "error", "message": "User not found."}), 404

        # Strip sensitive data (like password_hash) before returning
        user_record.pop('password_hash', None)
        
        return jsonify({
            "status": "success",
            "data": user_record
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@v2_blueprint.route('/admin/dashboard', methods=['POST'])
@enforce_login_security
@token_required
def admin_login(current_user_id):
    """Secure administrative endpoint enforcing explicit whitelist constraints."""
    # Note: Using both decorators. current_user_id is passed by token_required.
    
    # Re-verify email from the whitelist using the ID (restoring the email)
    email = current_user_id.replace(',', '.')

    if email not in ADMIN_EMAIL_WHITELIST:
        return jsonify({"status": "error", "message": "Unauthorized administrative access."}), 403

    # Proceed with strict administrative authentication...
    return jsonify({"status": "success", "message": "Administrative access granted."}), 200
