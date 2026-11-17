#!/usr/bin/env python3
"""Import many albums into the LIVE site, processing on THIS machine, with one progress meter.

    uv run python deploy/import_batch.py albums.tsv [--only slug,slug] [--workers N] [--yes] [--log FILE]

albums.tsv has one album per line: slug, "title", folder (any whitespace between; quote the title;
# starts a comment).

How it works: your cores resize each photo (WebP sizes, blur-up placeholder, GPS stripped from the
original) and upload the files straight to the live Space. Only the finished database rows are sent
to the droplet (over SSH, in small batches), because the database lives there. Nothing is copied to
the droplet's disk. Re-running is safe: photos already in an album (same file content) are skipped.

Uses the Space keys in this project's .env (the live bucket is checked against the server's, and the
`dev` prefix is NOT used). Run it in a real terminal (not a `!` shell) for the live meter; otherwise
it prints a status line every 30 seconds. Ctrl-C stops cleanly and records what was already uploaded.

Settings: UCT_HOST (default root@143.110.171.187), UCT_KEY (default ~/.ssh/id_digital_ocean_nov26).
"""
import argparse
import hashlib
import json
import math
import os
import shlex
import signal
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOST = os.environ.get("UCT_HOST", "root@143.110.171.187")
KEY = os.environ.get("UCT_KEY", str(Path.home() / ".ssh" / "id_digital_ocean_nov26"))
SSH = ["ssh", "-i", KEY, "-o", "IdentitiesOnly=yes", "-o", "ServerAliveInterval=30"]
PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}  # keep in sync with app/importer.py
TAU = 60.0  # seconds: how slowly the "now" speeds and the ETA react to change
BATCH = 40  # photos per database registration
BATCH_SECONDS = 60


# ---- Input ------------------------------------------------------------------------------------
@dataclass
class Album:
    slug: str
    title: str
    folder: Path
    files: dict = field(default_factory=dict)  # photo filename -> bytes
    todo: list = field(default_factory=list)  # [(name, size, sha256, sort_order)] still to import
    skipped: int = 0  # already on the server, or an identical file earlier in the folder
    skipped_bytes: int = 0

    @property
    def count(self):
        return len(self.files)

    @property
    def bytes(self):
        return sum(self.files.values())

    @property
    def todo_bytes(self):
        return sum(t[1] for t in self.todo)


def parse_list(text):
    albums = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = shlex.split(line)
        if len(parts) != 3:
            raise ValueError(f"line {n}: expected slug, \"title\", folder; got {len(parts)} fields")
        albums.append(Album(parts[0], parts[1], Path(parts[2]).expanduser()))
    return albums


def scan(album):
    if not album.folder.is_dir():
        raise ValueError(f"{album.slug}: folder not found: {album.folder}")
    album.files = {p.name: p.stat().st_size for p in sorted(album.folder.iterdir())
                   if p.is_file() and p.suffix.lower() in PHOTO_SUFFIXES}
    if not album.files:
        raise ValueError(f"{album.slug}: no photos in {album.folder}")


def sha_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def plan(album, known_hashes, max_order, hashes):
    """Decide what to import. Photos keep filename order, numbered after what the album already has;
    anything already on the server (or repeated within the folder) is skipped."""
    seen = set(known_hashes)
    order = max_order
    album.todo, album.skipped, album.skipped_bytes = [], 0, 0
    for name, size in album.files.items():  # filename order
        sha = hashes[name]
        if sha in seen:
            album.skipped += 1
            album.skipped_bytes += size
            continue
        seen.add(sha)
        order += 1
        album.todo.append((name, size, sha, order))


# ---- Formatting -------------------------------------------------------------------------------
def fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000


def fmt_dur(seconds):
    s = int(max(0, seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def bar(frac, width=24):
    filled = round(max(0.0, min(1.0, frac)) * width)
    return "█" * filled + "░" * (width - filled)


# ---- Throughput -------------------------------------------------------------------------------
class Phase:
    """Throughput of one kind of work, counted in bytes and items.

    `rate` is exponentially smoothed (time constant TAU) so one slow photo or a stalled second
    doesn't make the numbers jump; `avg` is the total over the whole running time.
    """

    def __init__(self):
        self.bytes = self.items = 0
        self.rate = self.irate = None  # smoothed bytes/s and items/s
        self.active = 0.0
        self._t = None
        self._b = self._i = 0

    def start(self, now):
        self._t, self._b, self._i = now, self.bytes, self.items

    def update(self, now, bytes_total, items_total):
        """Record cumulative progress. Rate samples need >= 1 s so tiny gaps don't spike."""
        self.bytes, self.items = bytes_total, items_total
        dt = now - self._t
        if dt < 1.0:
            return
        alpha = 1 - math.exp(-dt / TAU)
        b, i = (bytes_total - self._b) / dt, (items_total - self._i) / dt
        self.rate = b if self.rate is None else self.rate + alpha * (b - self.rate)
        self.irate = i if self.irate is None else self.irate + alpha * (i - self.irate)
        self.active += dt
        self._t, self._b, self._i = now, bytes_total, items_total

    def _elapsed(self, now):
        return self.active + (now - self._t if self._t is not None else 0)

    def avg(self, now):
        e = self._elapsed(now)
        return self.bytes / e if e >= 1 and self.bytes else None

    def avg_items(self, now):
        e = self._elapsed(now)
        return self.items / e if e >= 1 and self.items else None


class Progress:
    """Everything the meter shows. `work` counts source photo bytes finished; `net` counts bytes
    actually uploaded to the Space (the WebP sizes plus the original)."""

    def __init__(self, albums, now, workers=1):
        self.albums = albums
        self.workers = workers
        self.total_bytes = sum(a.todo_bytes for a in albums)
        self.total_items = sum(len(a.todo) for a in albums)
        self.work, self.net = Phase(), Phase()
        self.t0 = now
        self.index = 0
        self.stage = "starting"
        self.album_done = 0
        self.ok = self.failed = 0
        self.skipped = sum(a.skipped for a in albums)
        self.last = ""

    @property
    def album(self):
        return self.albums[self.index]

    def eta(self):
        """Seconds left at the smoothed speed; None until there is a speed."""
        left = self.total_bytes - self.work.bytes
        if left <= 0:
            return 0.0
        return left / self.work.rate if self.work.rate else None

    def fraction(self):
        return self.work.bytes / self.total_bytes if self.total_bytes else 1.0

    def lines(self, now):
        eta = self.eta()
        finish = (datetime.now() + timedelta(seconds=eta)).strftime("%H:%M") if eta else None
        a = self.album
        n = len(a.todo)
        frac_album = self.album_done / n if n else 1.0
        busy = min(self.workers, n - self.album_done) if self.stage == "processing" else 0

        def speeds(phase, photos):
            if phase.rate is None:
                return "—"
            now_, avg = f"now {fmt_bytes(phase.rate)}/s", f"avg {fmt_bytes(phase.avg(now) or 0)}/s"
            if photos:
                now_ += f" ({phase.irate * 60:.0f} photos/min)"
                avg += f" ({(phase.avg_items(now) or 0) * 60:.0f}/min)"
            return f"{now_}   {avg}"

        eta_text = "estimating…" if eta is None else fmt_dur(eta) + (f"  (done about {finish})" if finish else "")
        return [
            (f"Overall  {bar(self.fraction())} {self.fraction() * 100:5.1f}%   "
             f"{self.work.items} / {self.total_items} photos   "
             f"{fmt_bytes(self.work.bytes)} / {fmt_bytes(self.total_bytes)}"),
            (f"Album {self.index + 1}/{len(self.albums)}  {a.slug}  {self.stage}  {bar(frac_album, 16)} "
             f"{frac_album * 100:3.0f}%  {self.album_done} / {n} photos"),
            f"Elapsed  {fmt_dur(now - self.t0)}    ETA {eta_text}",
            f"Process  {speeds(self.work, True)}",
            f"Upload   {speeds(self.net, False)}   total {fmt_bytes(self.net.bytes)}",
            (f"workers busy {busy}/{self.workers}   ok {self.ok}   skipped {self.skipped}   "
             f"failed {self.failed}   last: {self.last or '—'}"),
        ]

    def status_line(self, now):
        eta = self.eta()
        return (f"[{fmt_dur(now - self.t0)}] {self.fraction() * 100:.0f}%  album {self.index + 1}/{len(self.albums)} "
                f"{self.album.slug} {self.stage}  photos {self.work.items}/{self.total_items}  "
                f"ETA {fmt_dur(eta) if eta is not None else '…'}  failed {self.failed}")


# ---- Display ----------------------------------------------------------------------------------
class Display:
    """Redraws the meter in place on a terminal; prints a status line every 30 s otherwise."""

    def __init__(self, progress):
        self.p = progress
        self.tty = sys.stdout.isatty()
        self.height = 0
        self._last_plain = 0.0

    def _erase(self):
        if self.tty and self.height:
            sys.stdout.write(f"\x1b[{self.height}F\x1b[J")
            self.height = 0

    def draw(self, now, force=False):
        if self.tty:
            self._erase()
            lines = self.p.lines(now)
            width = os.get_terminal_size().columns - 1
            sys.stdout.write("\n".join(line[:width] for line in lines) + "\n")
            self.height = len(lines)
            sys.stdout.flush()
        elif force or now - self._last_plain >= 30:
            self._last_plain = now
            print(self.p.status_line(now), flush=True)

    def say(self, msg, now):
        self._erase()
        print(msg, flush=True)
        self.draw(now)


# ---- Worker processes (one photo at a time) ---------------------------------------------------
_storage = None


def init_worker(cfg):
    global _storage
    from app.storage import Storage, make_client
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # the main process decides how to stop
    _storage = Storage(make_client(cfg["region"], cfg["key"], cfg["secret"]), cfg["bucket"],
                       cfg["public_base_url"], cfg["prefix"])


def work(job):
    """Process and upload one photo. Returns {"record", "sent"} or {"error"}; never raises."""
    from app.images import process
    from app.importer import photo_base_key, record_of, upload_photo

    path, slug, sha, sort_order = job
    base_key = photo_base_key(slug, Path(path).name, sha)
    try:
        p = process(Path(path).read_bytes())
        try:
            sent = upload_photo(_storage, base_key, p, Path(path).name)
        except Exception:
            _storage.delete_prefix(base_key)  # no orphaned files from a half-uploaded photo
            raise
        return {"name": Path(path).name, "record": record_of(p, base_key, sha, sort_order), "sent": sent}
    except Exception as e:  # noqa: BLE001 - report any failure for this photo and carry on
        return {"name": Path(path).name, "error": f"{type(e).__name__}: {e}"}


# ---- The server --------------------------------------------------------------------------------
def remote_flask(args, stdin=None):
    """Run `flask <args>` on the droplet with the live settings (same as deploy/import-album.sh)."""
    cmd = ("systemd-run --quiet --wait --pipe --collect -p User=uctballroom -p Group=uctballroom -p Nice=10 "
           "-p EnvironmentFile=/srv/uctballroom/.env -p Environment=FLASK_SKIP_DOTENV=1 "
           "-p WorkingDirectory=/srv/uctballroom/app "
           f"/srv/uctballroom/app/.venv/bin/flask --app app {' '.join(shlex.quote(a) for a in args)}")
    r = subprocess.run([*SSH, HOST, cmd], check=False, input=stdin, capture_output=True, text=True,
                       timeout=300)
    if r.returncode:
        raise RuntimeError(f"server command failed ({r.returncode}): {(r.stderr or r.stdout).strip()[-400:]}")
    return r.stdout.strip()


def server_state(slug):
    return json.loads(remote_flask(["album-state", slug]).splitlines()[-1])


def register(album, records, log):
    """Send finished photo rows to the server, retrying a few times. Returns the server's reply."""
    payload = "".join(json.dumps(r) + "\n" for r in records)
    for attempt in range(3):
        try:
            return remote_flask(["register-photos", "--album", album.slug, "--title", album.title], payload)
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            log.write(f"register attempt {attempt + 1} failed: {e}\n")
            log.flush()
            time.sleep(5 * (attempt + 1))
    log.write("UPLOADED BUT NOT REGISTERED (base keys):\n" + "".join(f"  {r['base_key']}\n" for r in records))
    log.flush()
    raise RuntimeError("could not register photos on the server (their base keys are in the log)")


def submit(pool, a):
    """Queue every photo of an album on the worker pool; returns {future: (name, size)}."""
    return {pool.submit(work, (str(a.folder / name), a.slug, sha, order)): (name, size)
            for name, size, sha, order in a.todo}


def run_album(p, disp, log, a, jobs, consume=True, now=time.monotonic):
    """Collect an album's finished photos and register them on the server in batches.

    The caller queues the NEXT album's jobs before calling this, so the workers never idle at an
    album boundary. consume=False only settles (records) whatever already finished: used after an
    interrupt for albums that were queued but not reached."""
    pending, last_flush, handled = [], now(), set()

    def take(fut):
        """Account for one finished photo."""
        handled.add(fut)
        name, size = jobs[fut]
        res = fut.result()
        p.album_done += 1
        p.last = name
        if "error" in res:
            p.failed += 1
            log.write(f"FAILED  {name}: {res['error']}\n")
            disp.say(f"  FAILED {name}: {res['error']}", now())
        else:
            p.ok += 1
            pending.append(res["record"])
            log.write(f"ok      {name} -> {res['record']['base_key']}\n")
            p.net.update(now(), p.net.bytes + res["sent"], p.net.items + 1)
        # Failed photos still count as finished work so the bar and ETA keep moving.
        p.work.update(now(), p.work.bytes + size, p.work.items + 1)

    def flush():
        nonlocal pending, last_flush
        if pending:
            p.stage = "registering"
            disp.draw(now())
            reply = register(a, pending, log)
            log.write(f"registered {len(pending)} ({reply})\n")
            log.flush()
            pending, p.stage = [], "processing"
        last_flush = now()

    try:
        for fut in as_completed(jobs) if consume else ():
            take(fut)
            if len(pending) >= BATCH or (pending and now() - last_flush >= BATCH_SECONDS):
                flush()
            disp.draw(now())
    finally:
        for fut in jobs:
            fut.cancel()  # on Ctrl-C or an error: don't start more photos
        wait([f for f in jobs if not f.cancelled()])  # photos already in flight still finish uploading
        for fut in jobs:
            if fut.done() and not fut.cancelled() and fut not in handled:
                take(fut)
        try:
            flush()  # record everything that did get uploaded
        except Exception as e:  # noqa: BLE001 - don't mask the original error
            print(f"Could not register the last photos: {e}", file=sys.stderr)


def local_storage_config(server):
    """Spaces settings from this project's .env, checked against what the live server uses."""
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    cfg = {"region": os.environ.get("SPACES_REGION"), "key": os.environ.get("SPACES_KEY"),
           "secret": os.environ.get("SPACES_SECRET"), "bucket": os.environ.get("SPACES_BUCKET"),
           "public_base_url": os.environ.get("SPACES_PUBLIC_BASE_URL") or "https://unused.invalid",
           "prefix": server["prefix"] or ""}
    missing = [k for k in ("region", "key", "secret", "bucket") if not cfg[k]]
    if missing:
        sys.exit(f"Missing Spaces settings in .env: {', '.join(missing)}")
    if (cfg["bucket"], cfg["region"]) != (server["bucket"], server["region"]):
        sys.exit(f"Bucket mismatch: this machine uses {cfg['bucket']}/{cfg['region']} but the live server uses "
                 f"{server['bucket']}/{server['region']}. Refusing to upload to the wrong place.")
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("list", type=Path)
    ap.add_argument("--only", help="comma-separated slugs to import (default: all)")
    ap.add_argument("--workers", type=int, default=max(1, min(6, (os.cpu_count() or 2) - 2)),
                    help="photos processed in parallel (default: up to 6; each needs ~300 MB RAM)")
    ap.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    ap.add_argument("--log", type=Path, default=Path("import-batch.log"))
    args = ap.parse_args(argv)

    albums = parse_list(args.list.read_text())
    if args.only:
        wanted = set(args.only.split(","))
        albums = [a for a in albums if a.slug in wanted]
    if not albums:
        sys.exit("No albums to import.")
    for a in albums:
        scan(a)

    print("Checking the server and fingerprinting files…", flush=True)
    states = {a.slug: server_state(a.slug) for a in albums}
    cfg = local_storage_config(next(iter(states.values())))
    with ThreadPoolExecutor(8) as ex:
        for a in albums:
            hashes = dict(zip(a.files, ex.map(sha_of, (a.folder / n for n in a.files))))
            st = states[a.slug]
            plan(a, st["hashes"], st["max_order"], hashes)

    print(f"\n{'album':24} {'photos':>7} {'new':>6} {'already in':>10} {'to upload':>10}")
    for a in albums:
        print(f"{a.slug:24} {a.count:>7} {len(a.todo):>6} {a.skipped:>10} {fmt_bytes(a.todo_bytes):>10}")
    todo = sum(len(a.todo) for a in albums)
    print(f"{'TOTAL':24} {sum(a.count for a in albums):>7} {todo:>6} {sum(a.skipped for a in albums):>10} "
          f"{fmt_bytes(sum(a.todo_bytes for a in albums)):>10}")
    print(f"\nInto the LIVE site: bucket {cfg['bucket']} ({cfg['region']}), key prefix "
          f"{cfg['prefix'] or '(none)'}, database on {HOST}. Processing here with {args.workers} workers.")
    if not todo:
        sys.exit("Nothing new to import.")
    if not args.yes:
        try:
            answer = input("Continue? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer != "y":
            sys.exit("Cancelled.")

    albums = [a for a in albums if a.todo]
    p = Progress(albums, time.monotonic(), args.workers)
    p.work.start(p.t0)
    p.net.start(p.t0)
    disp = Display(p)
    stats = []
    code = 0
    with args.log.open("a") as log, ProcessPoolExecutor(args.workers, initializer=init_worker,
                                                          initargs=(cfg,)) as pool:
        log.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} batch of {len(albums)} albums ===\n")
        try:
            queued = {}
            try:
                for i, a in enumerate(albums):
                    for j in (i, i + 1):  # keep the next album queued behind this one
                        if j < len(albums) and j not in queued:
                            queued[j] = submit(pool, albums[j])
                    p.index, p.album_done, p.stage = i, 0, "processing"
                    started, ok0, fl0 = time.monotonic(), p.ok, p.failed
                    log.write(f"--- {a.slug}\n")
                    run_album(p, disp, log, a, queued.pop(i))
                    stats.append((a.slug, p.ok - ok0, a.skipped, p.failed - fl0, time.monotonic() - started))
            finally:
                for j, jobs in sorted(queued.items()):  # queued but never reached: record what finished
                    p.index = j
                    run_album(p, disp, log, albums[j], jobs, consume=False)
        except KeyboardInterrupt:
            disp.say("Interrupted. Photos already uploaded were recorded; re-run to continue "
                     "(photos already imported are skipped).", time.monotonic())
            code = 130
        except Exception as e:  # noqa: BLE001 - report any failure, then stop cleanly
            disp.say(f"STOPPED on {p.album.slug}: {e}. Re-run to continue.", time.monotonic())
            code = 1
        pool.shutdown(wait=True, cancel_futures=True)
    if code:
        sys.exit(code)
    p.stage = "done"
    disp.draw(time.monotonic(), force=True)
    print(f"\n{'album':24} {'ok':>5} {'skipped':>8} {'failed':>7} {'time':>9}")
    for slug, ok, sk, fl, secs in stats:
        print(f"{slug:24} {ok:>5} {sk:>8} {fl:>7} {fmt_dur(secs):>9}")
    print(f"Total time {fmt_dur(time.monotonic() - p.t0)}. Log: {args.log}")
    sys.exit(1 if p.failed else 0)


if __name__ == "__main__":
    main()
