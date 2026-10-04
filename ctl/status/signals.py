"""Wake the worker's observer when a job advances, without probing in a request."""

from threading import Event

changed = Event()
