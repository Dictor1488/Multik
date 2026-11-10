# -*- coding: utf-8 -*-
"""INQ Multik: single-file runtime scaffold for the region changer.

Server registration and login hooks will be implemented here.
This scaffold deliberately does not change the client.
"""

from __future__ import absolute_import

import logging

MOD_ID = 'me.inq.multik'
_logger = logging.getLogger(MOD_ID)
_initialized = False


def init():
    global _initialized
    if _initialized:
        return
    _initialized = True
    _logger.info(
        '[INQ Multik] Project scaffold loaded; region switching is not implemented.'
    )


def fini():
    global _initialized
    _initialized = False
