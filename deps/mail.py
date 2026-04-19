"""Email utilities"""

from fastapi_mail import ConnectionConfig, MessageSchema, FastMail

from conf import settings
from deps.logs import logger

def load_mail_config() -> ConnectionConfig | None:
    """Load mail configuration"""
    if settings.mail_server:
        return ConnectionConfig(
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
    return None


async def send_email(subject: str, body: str, attachments: list = None):
    """Send an email"""
    if not (mail_config := load_mail_config()):
        logger.warning("Mail server not configured, skipping email")
        return

    message = MessageSchema(
        subject=subject,
        body=body,
        subtype=MessageSchema.subtype.plain,
        recipients=settings.mail_to,
        attachments=attachments if attachments else [],
    )

    fm = FastMail(mail_config)
    await fm.send_message(message)
