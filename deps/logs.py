""" Create a standard logger """
import logging
from conf import settings

logging.basicConfig(level=settings.log_level, format="%(levelname)s:\t%(message)s")
logger = logging.getLogger()
