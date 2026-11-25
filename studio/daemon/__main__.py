"""python -m studio.daemon   ->  starts the local server and opens the browser. A second start opens the running studio again."""
import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser


def run_file():
    from ..core.db import data_dir
    return data_dir() / "run.json"


def open_running(port) -> str | None:
    """The address that opens the studio which already runs on this data folder, or None."""
    try:
        r = json.loads(run_file().read_text(encoding="utf-8"))
        if r["port"] != port:
            return None
        q = urllib.request.Request(f"http://127.0.0.1:{port}/reopen", data=r["token"].encode(), method="POST", headers={"Origin": f"http://127.0.0.1:{port}", "x-studio": "1", "Content-Type": "text/plain"})
        with urllib.request.urlopen(q, timeout=10) as a:
            return f"http://127.0.0.1:{port}/launch?code={json.loads(a.read())['code']}"
    except (OSError, ValueError, KeyError):
        return None


def show(url):
    if "--no-browser" in sys.argv:
        print(url, flush=True)
    else:
        webbrowser.open(url)


def wait_until_up(port, s):
    for _ in range(600):
        if s.started:
            return True
        time.sleep(.1)
    return False


def main():
    from ..core.migrate import Damaged, SchemaProblem
    from .app import PORT, create
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", PORT)) == 0:
            url = open_running(PORT)
            if url:
                show(url)
                print("The studio is already running; it has been opened in the browser. Its own window must stay open.", flush=True)
                time.sleep(0 if "--no-browser" in sys.argv else 4)
                return 0
            print(f"Something else is using port {PORT} on this computer, so the studio cannot start.")
            print("If an older studio window is open, close it. Otherwise restart the computer, then try again.")
            return 1
    print("Starting the studio...", flush=True)
    try:
        app = create()
    except Damaged as e:
        from ..core import backup
        from ..core.db import data_dir
        print(f"\nThe studio did not start, and changed nothing: {e}.\n")
        print("Your work is safest if you restore the newest copy into a NEW folder and look at it there:")
        print(f"    .venv\\Scripts\\python -m studio.recover list {backup.DEFAULT}")
        print(f"    .venv\\Scripts\\python -m studio.recover restore {backup.DEFAULT} <copy> C:\\studio-restored")
        print(f"The damaged folder stays where it is: {data_dir().resolve()}")
        return 1
    except SchemaProblem as e:
        print(f"\nThe studio did not start, and changed nothing: {e}.")
        return 1
    except Exception as e:
        from ..core.jobs import log_error
        ref = log_error("start", traceback.format_exc())
        print(f"\nThe studio did not start: {type(e).__name__}: {e}")
        print(f"Nothing you made was changed. Evidence number {ref} is in data\\errors.log.")
        return 1
    server = uvicorn_server(app, PORT)
    app.state.server = server
    run_file().write_text(json.dumps({"pid": os.getpid(), "port": PORT, "token": app.state.reopen}), encoding="utf-8")

    def hello():
        if wait_until_up(PORT, server):
            show(f"http://127.0.0.1:{PORT}/launch?code={app.state.launch}")
    threading.Thread(target=hello, daemon=True).start()
    print(f"The studio is running at http://127.0.0.1:{PORT} (this computer only).", flush=True)
    print("Leave this window open while you work. To stop: 'Stop the studio' at the bottom of the menu, or close this window.", flush=True)
    print("If you close the browser by mistake, double-click Start Studio.bat again; it opens the running studio.", flush=True)
    if app.state.migrated.get("copy"):
        print(f"The database was brought forward; the version before is kept at {app.state.migrated['copy']}", flush=True)
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    app.state.jobs.stop()
    run_file().unlink(missing_ok=True)
    if not server.started:
        print("The studio could not open its address. Try again in a minute.")
        return 1
    print("The studio has stopped. Nothing is lost.", flush=True)
    return 0


def uvicorn_server(app, port):
    import uvicorn
    return uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level=os.environ.get("STUDIO_LOG", "warning"), access_log=False, server_header=False, timeout_graceful_shutdown=8))


if __name__ == "__main__":
    sys.exit(main())
