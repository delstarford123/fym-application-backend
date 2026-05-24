import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

class EmailManager:
    @staticmethod
    def send_otp(recipient_email, otp_code):
        """
        Sends a 6-digit OTP to the student's email using Gmail SMTP.
        """
        sender_email = os.getenv('MAIL_USERNAME')
        sender_password = os.getenv('MAIL_PASSWORD')
        
        if not sender_email or not sender_password:
            print("[ERROR] Email credentials not found in environment.")
            return False

        subject = f"FYM Verification Code: {otp_code}"
        body = f"""
        <html>
        <body style="font-family: sans-serif; padding: 20px;">
            <h2 style="color: #8B5CF6;">Welcome to FYM</h2>
            <p>Your institutional verification protocol has been initiated.</p>
            <div style="background: #f3f4f6; padding: 20px; border-radius: 12px; text-align: center; margin: 20px 0;">
                <span style="font-size: 32px; font-weight: bold; letter-spacing: 5px; color: #1f2937;">{otp_code}</span>
            </div>
            <p style="color: #6b7280; font-size: 14px;">This code will expire in 10 minutes. If you did not request this, please ignore this email.</p>
            <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 20px 0;">
            <p style="font-size: 12px; color: #9ca3af;">FYM - AI-Powered Student Network</p>
        </body>
        </html>
        """

        msg = MIMEMultipart()
        msg['From'] = f"FYM Campus Support <{sender_email}>"
        msg['To'] = recipient_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))

        try:
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
            server.quit()
            print(f"[EMAIL] OTP successfully sent to {recipient_email}")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to send email: {e}")
            return False
