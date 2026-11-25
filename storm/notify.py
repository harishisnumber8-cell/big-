"""Send email over SMTP (Amazon SES, Postmark and others all offer SMTP) and SMS through Twilio's REST API.
Without credentials in the environment, messages are printed instead of sent."""
import os
import smtplib
from email.message import EmailMessage

import requests


def send(channel, to, subject, body):
    if channel == "email":
        _email(to, subject, body)
    else:
        _sms(to, body)


def _email(to, subject, body):
    host = os.environ.get("SMTP_HOST")
    if not host:
        print(f"[dry run] email to {to}: {subject}\n{body}\n")
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = os.environ["FROM_EMAIL"], to, subject
    msg.set_content(body)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        s.send_message(msg)


def _sms(to, body):
    sid = os.environ.get("TWILIO_SID")
    if not sid:
        print(f"[dry run] SMS to {to}: {body}\n")
        return
    r = requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                      auth=(sid, os.environ["TWILIO_TOKEN"]),
                      data={"From": os.environ["TWILIO_FROM"], "To": to, "Body": body}, timeout=30)
    r.raise_for_status()
