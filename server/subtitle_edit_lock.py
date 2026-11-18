"""Admission lock shared by subtitle edits and pipeline start endpoints."""

from threading import RLock


EDIT_ADMISSION_LOCK = RLock()
