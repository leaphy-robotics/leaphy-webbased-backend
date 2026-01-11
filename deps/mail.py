"""Email utilities"""

from fastapi_mail import ConnectionConfig, MessageSchema, FastMail

from conf import settings
from deps.logs import logger

MAIL_CONFIG = None

if settings.mail_server:
    MAIL_CONFIG = ConnectionConfig(
        MAIL_SERVER=settings.mail_server,
        MAIL_PORT=settings.mail_port,
        MAIL_USERNAME=settings.mail_username,
        MAIL_PASSWORD=settings.mail_password,
        MAIL_FROM_NAME=settings.mail_from,
        MAIL_STARTTLS=True,
        MAIL_SSL_TLS=False,
        MAIL_FROM=settings.mail_from,
        USE_CREDENTIALS=True,
    )


async def send_email(subject: str, body: str, attachments: list = None):
    """Send an email"""
    if not MAIL_CONFIG:
        logger.warning("Mail server not configured, skipping email")
        return

    message = MessageSchema(
        subject=subject,
        body=body,
        subtype="plain",
        recipients=settings.mail_to,
        attachments=attachments,
    )

    fm = FastMail(MAIL_CONFIG)
    await fm.send_message(message)
