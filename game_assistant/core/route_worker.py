"""Route searches in their own process, so they can never slow Navi down.

Python runs one thread of Python code at a time: a long route search on a thread of the assistant
would steal time from the fairy's movement (60 frames a second) and from streaming answers. In a
separate process (below-normal priority, so the game keeps the CPU it needs) it can take its time:
answers stay instant, and only setting off along a new route waits for it.

The worker builds its mesh source once (`factory`: "package.module:Class", created with no
arguments) and keeps it, so blocks it has loaded stay loaded between routes.
"""
from __future__ import annotations

import ctypes
import importlib
import itertools
import multiprocessing as mp
import queue
import sys
import threading

from .navmesh import MeshRoute, search

BELOW_NORMAL_PRIORITY_CLASS = 0x4000


def _main(factory: str, requests, results) -> None:
    if sys.platform == 'win32':
        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
    mod, _, cls = factory.partition(':')
    source = getattr(importlib.import_module(mod), cls)()
    while True:
        job = requests.get()
        if job is None:
            return
        if job[0] == 'observe':  # where the player is now (no answer): the source may learn from it
            if hasattr(source, 'observe'):
                try:
                    source.observe(**job[1])
                except Exception:
                    pass
            continue
        _, rid, kwargs = job
        try:
            for k, v in kwargs.pop('attrs', {}).items():  # e.g. which world map the search is on
                setattr(source, k, v)
            r = search(source, **kwargs)
            results.put((rid, r, None))
        except Exception as e:  # report, keep serving
            results.put((rid, None, f'{type(e).__name__}: {e}'))


class RouteWorker:
    def __init__(self, factory: str):
        ctx = mp.get_context('spawn')
        self._req, self._res = ctx.Queue(), ctx.Queue()
        self._proc = ctx.Process(target=_main, args=(factory, self._req, self._res), daemon=True,
                                 name='route planner')
        self._proc.start()
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self.last_error: str | None = None

    def route(self, start, goal, timeout: float = 30.0, attrs: dict | None = None, **kwargs) -> MeshRoute | None:
        """One search (see core.navmesh.search for kwargs: budget_s, weight...). Waits for it."""
        with self._lock:  # one search at a time; answers come back in order
            rid = next(self._ids)
            self._req.put(('route', rid, dict(start=tuple(start), goal=tuple(goal), attrs=attrs or {}, **kwargs)))
            waited = 0.0
            while True:
                try:
                    got, r, err = self._res.get(timeout=0.25)
                except queue.Empty:
                    waited += 0.25
                    if not self._proc.is_alive():
                        self.last_error = 'the route planner stopped'
                        return None
                    if waited >= timeout:
                        self.last_error = 'the route planner did not answer in time'
                        return None
                    continue
                if got == rid:
                    self.last_error = err
                    return r

    def observe(self, **kwargs) -> None:
        """Tell the planner where the player is (it learns links from their moves). Never waits."""
        try:
            self._req.put(('observe', kwargs))
        except Exception:
            pass

    @property
    def alive(self) -> bool:
        return self._proc.is_alive()

    def close(self) -> None:
        try:
            self._req.put(None)
        except Exception:
            pass
