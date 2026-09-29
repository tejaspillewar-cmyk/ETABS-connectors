"""
Shared ETABS COM connection plumbing, used by both etabs_gui.pyw (Tkinter)
and dxf_model_builder.py (PySide6/Qt).

Why a single worker thread
--------------------------
Every ETABS call runs on one long-lived background thread that calls
CoInitialize once. A COM pointer belongs to the apartment that created it,
so creating SapModel on one thread and using it on another is what makes
these scripts fail intermittently. One thread also means ETABS is never
asked to do two things at once, and the window stays responsive.

Deliberately free of any GUI toolkit import -- EtabsWorker and etabs_helper
are lifted verbatim from etabs_gui.pyw, which already proved this out.
"""
import os
import queue
import sys
import threading
import time
import traceback

import app_paths


def _write_crash_log(text):
    """Append a timestamped report; returns the path, or "" if we could not.

    Not print_exc(): stderr is None under pythonw, so that would raise a
    second error on top of this one.
    """
    try:
        if not app_paths.ensure_dir(app_paths.logs_dir()):
            return ""
        path = app_paths.logs_dir() / f"crash_{time.strftime('%Y%m%d_%H%M%S')}.log"
        header = (f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                  f"python     : {sys.version}\n"
                  f"executable : {sys.executable}\n"
                  f"argv       : {sys.argv}\n"
                  f"cwd        : {os.getcwd()}\n\n")
        path.write_text(header + text, encoding="utf-8")
        return str(path)
    except Exception:
        return ""


def etabs_helper():
    """Create the ETABS COM helper, explaining the usual failure.

    comtypes generates its ETABSv1 wrapper on first use; on a machine where
    ETABS was never installed (or registered badly) that surfaces as an
    AttributeError from deep inside comtypes, which tells the user nothing.
    The helper must be created BEFORE `import comtypes.gen.ETABSv1` -- that
    import is what triggers comtypes to generate/cache the wrapper module in
    the first place, so importing it first raises ModuleNotFoundError on a
    machine that has never run this before.
    """
    import comtypes.client
    try:
        helper = comtypes.client.CreateObject("ETABSv1.Helper")
        import comtypes.gen.ETABSv1
        return helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)
    except Exception as exc:
        raise RuntimeError(
            "Could not load the ETABS COM API (ETABSv1.Helper).\n\n"
            "This usually means ETABS is not installed on this machine, or "
            "its COM registration is damaged. Reinstalling or repairing ETABS "
            "normally fixes it.\n\n"
            f"Original error: {exc}") from exc


def attach_by_pid(pid: int):
    """Attach to a specific running ETABS instance by process id. Returns SapModel."""
    if not pid:
        raise RuntimeError("No ETABS instance was selected.")
    helper = etabs_helper()
    import comtypes.gen.ETABSv1
    obj = helper.GetObjectProcess("CSI.ETABS.API.ETABSObject", pid)
    if obj is None:
        raise RuntimeError(
            f"ETABS refused the connection (PID {pid}).\n\n"
            "Most often the model is still loading -- wait for ETABS to finish "
            "opening it and try again.")
    obj = obj.QueryInterface(comtypes.gen.ETABSv1.cOAPI)
    return obj.SapModel


class EtabsWorker:
    """Serialises every ETABS call onto a single CoInitialize'd thread."""

    def __init__(self):
        self._jobs = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="etabs-worker")
        self._thread.start()

    def submit(self, fn, on_done=None):
        """Queue fn() for the worker thread.

        on_done(ok, result_or_exception, elapsed_seconds) is called back on
        the worker thread; marshal it to the GUI thread yourself (e.g. Tk's
        .after(), or a Qt signal, which is thread-safe by construction).
        """
        self._jobs.put((fn, on_done))

    def _loop(self):
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass  # non-Windows, or comtypes missing -- jobs will report it

        while True:
            fn, on_done = self._jobs.get()
            started = time.perf_counter()
            try:
                result = fn()
                ok, payload = True, result
            except Exception as exc:
                ok, payload = False, exc
            elapsed = time.perf_counter() - started
            if on_done is not None:
                try:
                    on_done(ok, payload, elapsed)
                except Exception:
                    _write_crash_log(traceback.format_exc())
            self._jobs.task_done()
