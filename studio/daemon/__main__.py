"""python -m studio.daemon   ->  starts the local server and opens the browser once."""
import os
import socket
import sys
import threading
import time
import webbrowser

import uvicorn


def main():
    from ..core.migrate import Damaged, SchemaProblem
    from .app import PORT, create
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", PORT)) == 0:
            print("The studio is already running in another window. Close that window first.")
            return 1
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
    url = f"http://127.0.0.1:{PORT}/launch?code={app.state.launch}"
    if "--no-browser" in sys.argv:
        print(url, flush=True)
    else:
        threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(url)), daemon=True).start()
    print(f"The studio is running at http://127.0.0.1:{PORT} (this computer only). Close this window to stop it.", flush=True)
    if app.state.migrated.get("copy"):
        print(f"The database was brought forward; the version before is kept at {app.state.migrated['copy']}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level=os.environ.get("STUDIO_LOG", "warning"), access_log=False, server_header=False)
    app.state.jobs.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
