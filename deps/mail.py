"""Email utilities"""

from fastapi_mail import ConnectionConfig, MessageSchema, FastMail

from conf import settings

mail_config = ConnectionConfig(
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
    message = MessageSchema(
        subject=subject,
        body=body,
        subtype="plain",
        recipients=settings.mail_to,
        attachments=attachments,
    )

    fm = FastMail(mail_config)
    await fm.send_message(message)
