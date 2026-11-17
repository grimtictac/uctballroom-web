import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "import_batch", Path(__file__).resolve().parent.parent / "deploy" / "import_batch.py")
ib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ib)


def make_albums(tmp_path):
    """Albums a (2 photos, 4000 B) and b (1 photo, 6000 B), all new, numbered like a first import."""
    albums = []
    for slug, sizes in (("a", [1000, 3000]), ("b", [6000])):
        d = tmp_path / slug
        d.mkdir()
        for i, n in enumerate(sizes):
            (d / f"{i}.jpg").write_bytes(b"x" * n + bytes([i]))
        (d / "notes.txt").write_text("ignore me")
        a = ib.Album(slug, slug.upper(), d)
        ib.scan(a)
        ib.plan(a, [], 0, {n: ib.sha_of(d / n) for n in a.files})
        albums.append(a)
    return albums


def test_parse_list_handles_quotes_tabs_comments():
    text = '# c\niv26-part1    "Intervarsity 2026 Part 1"\t~/Pictures/x/\n\nm  "Medals 2026"  /tmp/m\n'
    a, b = ib.parse_list(text)
    assert (a.slug, a.title) == ("iv26-part1", "Intervarsity 2026 Part 1")
    assert a.folder == Path("~/Pictures/x/").expanduser() and b.title == "Medals 2026"
    with pytest.raises(ValueError):
        ib.parse_list("only-two fields")


def test_scan_counts_photos_only(tmp_path):
    a, b = make_albums(tmp_path)
    assert (a.count, a.bytes, b.count) == (2, 4002, 1)
    with pytest.raises(ValueError):
        ib.scan(ib.Album("x", "x", tmp_path / "missing"))


def test_formatting():
    assert ib.fmt_bytes(999) == "999 B" and ib.fmt_bytes(2_500_000) == "2.5 MB" and ib.fmt_bytes(10_240_000_000) == "10.2 GB"
    assert ib.fmt_dur(3725) == "1:02:05" and ib.fmt_dur(-3) == "0:00:00"
    assert ib.bar(0.5, 4) == "██░░" and ib.bar(2, 4) == "████"


def test_plan_numbers_in_filename_order_and_skips_known_and_repeats(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    for name, body in (("a.jpg", b"1"), ("b.jpg", b"2"), ("c.jpg", b"2"), ("d.jpg", b"4")):
        (d / name).write_bytes(body)
    a = ib.Album("x", "X", d)
    ib.scan(a)
    hashes = {n: ib.sha_of(d / n) for n in a.files}
    ib.plan(a, [hashes["a.jpg"]], 7, hashes)  # a.jpg is already on the server; c.jpg repeats b.jpg
    assert [(n, o) for n, _, _, o in a.todo] == [("b.jpg", 8), ("d.jpg", 9)]
    assert a.skipped == 2 and a.skipped_bytes == 2 and a.todo_bytes == 2


def test_phase_rate_is_smoothed_and_average_is_exact():
    ph = ib.Phase()
    ph.start(0)
    ph.update(10, 10_000, 5)       # 1000 B/s
    assert ph.rate == 1000 and ph.avg(10) == 1000
    ph.update(20, 10_000, 5)       # stalled 10 s: smoothed rate falls, but not to zero
    assert 0 < ph.rate < 1000
    assert ph.avg(20) == 500
    ph.update(20.3, 10_100, 6)     # sub-second sample ignored for rate, still counted
    assert ph.bytes == 10_100


def test_eta_needs_a_speed_then_counts_down(tmp_path):
    albums = make_albums(tmp_path)          # 10,003 source bytes to do
    p = ib.Progress(albums, 0, workers=4)
    assert p.eta() is None
    p.work.start(0)
    p.work.update(10, 1002, 1)              # 100.2 B/s
    assert p.eta() == pytest.approx(9001 / 100.2)
    p.work.update(20, 10_003, 3)
    assert p.eta() == 0.0 and p.fraction() == 1.0


def test_lines_render_at_every_stage(tmp_path):
    albums = make_albums(tmp_path)
    p = ib.Progress(albums, 0, workers=4)
    assert len(p.lines(0)) == 6 and "estimating" in p.lines(0)[2]
    p.stage = "processing"
    p.work.start(0); p.net.start(0)
    p.work.update(10, 1000, 1); p.net.update(10, 5000, 1)
    p.album_done, p.ok, p.last = 1, 1, "x.jpg"
    text = "\n".join(p.lines(10))
    assert "processing" in text and "now 100 B/s (6 photos/min)" in text and "now 500 B/s" in text
    assert "workers busy 1/4" in text and "last: x.jpg" in text and "1 / 2 photos" in text
    assert p.status_line(11).startswith("[0:00:11]")


def jpeg_bytes(w=1000, h=800):
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, "JPEG")
    return buf.getvalue()


def test_work_processes_uploads_and_returns_a_record(tmp_path):
    from fakes import FakeClient

    from app.storage import Storage
    f = tmp_path / "p.jpg"
    f.write_bytes(jpeg_bytes())
    client = FakeClient()
    ib._storage = Storage(client, "b", "https://cdn.example", "")
    res = ib.work((str(f), "home", "abc", 5))
    rec = res["record"]
    assert rec["sort_order"] == 5 and rec["sha256"] == "abc" and rec["variants"] == "400,800"
    assert rec["base_key"].startswith("albums/home/")
    assert set(client.objects) == {f"{rec['base_key']}/{n}" for n in ("400.webp", "800.webp", "original.jpg")}
    assert res["sent"] == sum(len(o["Body"]) for o in client.objects.values())


def test_work_reports_a_bad_file_instead_of_raising(tmp_path):
    from fakes import FakeClient

    from app.storage import Storage
    f = tmp_path / "bad.jpg"
    f.write_bytes(b"corrupt")
    ib._storage = Storage(FakeClient(), "b", "https://cdn.example", "")
    assert "cannot read image" in ib.work((str(f), "home", "abc", 1))["error"]


class InlinePool:
    """Runs jobs immediately, standing in for the process pool."""

    def submit(self, fn, job):
        from concurrent.futures import Future
        fut = Future()
        fut.set_result(fn(job))
        return fut


def test_run_album_registers_in_batches_and_counts_failures(tmp_path, monkeypatch):
    import io

    albums = make_albums(tmp_path)
    monkeypatch.setattr(ib, "BATCH", 1)
    registered = []
    monkeypatch.setattr(ib, "register", lambda album, recs, log: registered.append([r["sort_order"] for r in recs]) or "ok")
    results = iter([{"name": "0.jpg", "record": {"base_key": "k0", "sort_order": 1}, "sent": 500},
                    {"name": "1.jpg", "error": "ValueError: nope"}])
    monkeypatch.setattr(ib, "work", lambda job: next(results))
    p = ib.Progress(albums[:1], 0.0, workers=2)
    p.work.start(0.0); p.net.start(0.0)
    ib.run_album(p, ib.Display(p), io.StringIO(), albums[0], ib.submit(InlinePool(), albums[0]),
                 now=iter(float(i) for i in range(1, 100)).__next__)
    assert (p.ok, p.failed, p.album_done) == (1, 1, 2)
    assert registered == [[1]]
    assert p.work.items == 2 and p.work.bytes == 4002 and p.net.bytes == 500


def test_config_refuses_a_bucket_that_differs_from_the_servers(monkeypatch):
    monkeypatch.setenv("SPACES_BUCKET", "other")
    monkeypatch.setenv("SPACES_REGION", "lon1")
    monkeypatch.setenv("SPACES_KEY", "k")
    monkeypatch.setenv("SPACES_SECRET", "s")
    with pytest.raises(SystemExit, match="Bucket mismatch"):
        ib.local_storage_config({"bucket": "uctballroom", "region": "lon1", "prefix": ""})


def test_unreached_queued_album_still_gets_its_finished_photos_recorded(tmp_path, monkeypatch):
    import io

    albums = make_albums(tmp_path)
    registered = []
    monkeypatch.setattr(ib, "register", lambda album, recs, log: registered.append((album.slug, len(recs))) or "ok")
    monkeypatch.setattr(ib, "work", lambda job: {"name": Path(job[0]).name, "record": {"base_key": job[0]}, "sent": 1})
    jobs = ib.submit(InlinePool(), albums[1])  # album b was queued ahead, then the run was interrupted
    p = ib.Progress(albums, 0.0, workers=2)
    p.work.start(0.0); p.net.start(0.0)
    p.index = 1
    ib.run_album(p, ib.Display(p), io.StringIO(), albums[1], jobs, consume=False)
    assert registered == [("b", 1)] and p.ok == 1
