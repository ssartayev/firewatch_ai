"""
Assemble a PDF from rendered slide images.

Chrome's print engine mangles these slides: the canvas is a 1920x1080 block
scaled with a CSS transform, and in print it dropped images and whole flex
columns on several pages. Rather than fight it, this renders each slide the
same way the presenter does — a real browser at 1920x1080 — and writes those
pixels straight into a PDF. What you see on screen is then exactly what the
PDF contains, by construction.

    python scripts/slides_to_pdf.py <png-dir> <out.pdf> [order.json]
"""
from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path


def build(images: list[Path], out: Path, quality: int = 88) -> None:
    from PIL import Image

    objs: list[bytes] = []          # 1-indexed on write
    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)

    # Page size in points: 1920x1080 px at 96 dpi = 1440x810 pt. Use the
    # PowerPoint 16:9 size (960x540 pt) so it prints and imports cleanly.
    W, H = 960.0, 540.0

    kids: list[int] = []
    pages_id = len(images) * 3 + 2   # reserved, fixed below
    page_ids: list[int] = []

    contents = []
    for img_path in images:
        im = Image.open(img_path).convert("RGB")
        buf = BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True, progressive=False)
        data = buf.getvalue()

        img_obj = add(
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d "
            b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
            b"/Length %d >>\nstream\n" % (im.width, im.height, len(data))
            + data + b"\nendstream"
        )
        stream = b"q %f 0 0 %f 0 0 cm /Im0 Do Q" % (W, H)
        cont_obj = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        contents.append((img_obj, cont_obj))

    # pages tree and page objects
    pages_obj = add(b"")             # placeholder, filled after page ids known
    for img_obj, cont_obj in contents:
        pid = add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %f %f] "
            b"/Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R >>"
            % (pages_obj, W, H, img_obj, cont_obj)
        )
        page_ids.append(pid)

    objs[pages_obj - 1] = (
        b"<< /Type /Pages /Count %d /Kids [%s] >>"
        % (len(page_ids), b" ".join(b"%d 0 R" % p for p in page_ids))
    )
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_obj)

    # serialise
    out_buf = BytesIO()
    out_buf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, body in enumerate(objs, start=1):
        offsets.append(out_buf.tell())
        out_buf.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out_buf.tell()
    out_buf.write(b"xref\n0 %d\n" % (len(objs) + 1))
    out_buf.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out_buf.write(b"%010d 00000 n \n" % off)
    out_buf.write(
        b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objs) + 1, catalog, xref)
    )
    out.write_bytes(out_buf.getvalue())


def main() -> int:
    png_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
    if len(sys.argv) > 3:
        order = json.loads(Path(sys.argv[3]).read_text())["order"]
        images = [png_dir / f"{name}.png" for name in order]
    else:
        images = sorted(png_dir.glob("*.png"))
    missing = [p for p in images if not p.exists()]
    if missing:
        print("  missing renders:", [m.name for m in missing])
        return 1
    build(images, out)
    print(f"  {out.name:26} {out.stat().st_size/1024/1024:.1f} MB  ({len(images)} pages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
