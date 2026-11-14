import io

import pytest
from PIL import Image

from app.images import GPS_IFD, UnsupportedImage, process


def make_jpeg(size=(2000, 1000), orientation=None, gps=False, taken="2025:03:01 19:30:00"):
    img = Image.new("RGB", size, (200, 40, 40))
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    if taken:
        exif.get_ifd(0x8769)[0x9003] = taken
    if gps:
        g = exif.get_ifd(GPS_IFD)
        g[1] = "S"
        g[3] = "E"
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


def exif_of(data):
    return Image.open(io.BytesIO(data)).getexif()


def test_variants_and_dimensions():
    p = process(make_jpeg((2000, 1000)))
    assert (p.width, p.height) == (2000, 1000)
    assert sorted(p.variants) == [400, 800, 1600]
    for w, blob in p.variants.items():
        im = Image.open(io.BytesIO(blob))
        assert im.format == "WEBP" and im.size == (w, w // 2)


def test_never_upscales():
    p = process(make_jpeg((600, 400)))
    assert sorted(p.variants) == [400]
    p = process(make_jpeg((300, 200)))
    assert sorted(p.variants) == [300]


def test_orientation_applied_to_dimensions_and_variants():
    p = process(make_jpeg((2000, 1000), orientation=6))  # rotated 90deg
    assert (p.width, p.height) == (1000, 2000)
    im = Image.open(io.BytesIO(p.variants[800]))
    assert im.size == (800, 1600)


def test_taken_at_from_exif():
    assert process(make_jpeg()).taken_at == "2025-03-01T19:30:00"
    assert process(make_jpeg(taken=None)).taken_at is None
    assert process(make_jpeg(taken="garbage")).taken_at is None


def test_gps_stripped_from_original_but_date_kept():
    src = make_jpeg(gps=True, orientation=6)
    assert exif_of(src).get_ifd(GPS_IFD)
    p = process(src)
    out = exif_of(p.original)
    assert not out.get_ifd(GPS_IFD)
    assert out.get(0x0112) == 6  # orientation preserved in original
    assert p.taken_at == "2025-03-01T19:30:00"
    assert p.original_ext == "jpg"


def test_original_untouched_when_no_gps():
    src = make_jpeg()
    assert process(src).original == src


def test_png_supported():
    buf = io.BytesIO()
    Image.new("RGB", (900, 600)).save(buf, "PNG")
    p = process(buf.getvalue())
    assert p.original_ext == "png" and sorted(p.variants) == [400, 800]


def test_placeholder_is_small_data_uri():
    p = process(make_jpeg())
    assert p.placeholder.startswith("data:image/webp;base64,")
    assert len(p.placeholder) < 2000


def test_rejects_garbage():
    with pytest.raises(UnsupportedImage):
        process(b"not an image")
