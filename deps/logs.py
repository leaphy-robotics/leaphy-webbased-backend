""" Create a standard logger """
import logging

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s:\t%(message)s")
logger = logging.getLogger()
