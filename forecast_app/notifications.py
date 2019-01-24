import logging

from anymail.exceptions import AnymailError
from django.core.mail import send_mail


logger = logging.getLogger(__name__)


def send_notification_email(address, subject, message):
    """
    Sends an email per the passed args. Sends it immediately and in the current thread. In the future may enqueue
    the sending.
    """
    try:
        # todo xx pass None instead of hard-coded from_email so that Django will use DEFAULT_FROM_EMAIL
        send_mail(subject, message, "Zoltar <zoltar@example.com>", [address])
        logger.info("send_notification_email(): sent a message to: {}, subject: '{}'".format(address, subject))
    except AnymailError as ae:
        logger.error("send_notification_email(): failed to send message: {}".format(ae))
