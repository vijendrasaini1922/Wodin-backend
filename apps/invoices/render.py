"""Minimal, dependency-free invoice PDF renderer (Pillow). Replace with a templated renderer if branding requires."""
import io

from PIL import Image, ImageDraw, ImageFont

W, H, MARGIN = 1240, 1754, 90  # A4 @ 150 DPI
GREEN, INK, MUTED = (40, 61, 53), (31, 35, 40), (110, 110, 110)


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover
        return ImageFont.load_default()


def render(invoice) -> bytes:
    img = Image.new("RGB", (W, H), (244, 239, 230))
    d = ImageDraw.Draw(img)
    y = MARGIN
    d.text((MARGIN, y), "WoDin", fill=GREEN, font=_font(64)); y += 90
    d.text((MARGIN, y), "TAX INVOICE / RECEIPT", fill=INK, font=_font(34)); y += 60
    d.text((MARGIN, y), f"Invoice no: {invoice.number}", fill=INK, font=_font(28)); y += 40
    d.text((MARGIN, y), f"Date: {invoice.issued_at:%d %b %Y}", fill=INK, font=_font(28)); y += 40
    d.text((MARGIN, y), f"Order: {invoice.buyer['order_number']}", fill=INK, font=_font(28)); y += 70

    d.text((MARGIN, y), "Billed to", fill=MUTED, font=_font(24))
    d.text((W // 2, y), "Sold by", fill=MUTED, font=_font(24)); y += 36
    buyer, seller = invoice.buyer, invoice.seller
    left = [buyer.get("name") or "", buyer.get("phone") or "", buyer.get("email") or ""] + buyer.get("address_lines", [])
    right = [seller.get("name", ""), seller.get("address", ""), f"GSTIN: {seller['gstin']}" if seller.get("gstin") else ""]
    for i in range(max(len(left), len(right))):
        if i < len(left) and left[i]:
            d.text((MARGIN, y), left[i], fill=INK, font=_font(26))
        if i < len(right) and right[i]:
            d.text((W // 2, y), right[i], fill=INK, font=_font(26))
        y += 36
    y += 40
    d.line([(MARGIN, y), (W - MARGIN, y)], fill=GREEN, width=3); y += 14
    d.text((MARGIN, y), "Description", fill=MUTED, font=_font(24))
    d.text((W - MARGIN - 220, y), "Amount (INR)", fill=MUTED, font=_font(24)); y += 44
    for line in invoice.lines:
        d.text((MARGIN, y), str(line["label"])[:70], fill=INK, font=_font(26))
        d.text((W - MARGIN - 160, y), line["amount"], fill=INK, font=_font(26)); y += 40
    y += 10
    d.line([(MARGIN, y), (W - MARGIN, y)], fill=MUTED, width=1); y += 20
    t = invoice.totals
    rows = [("Subtotal", t["subtotal"]), ("Discount", f"-{t['discount']}"), ("Shipping", t["shipping"])]
    if float(t["tax"]) > 0:
        rows.append((f"Tax ({'included' if t['prices_include_tax'] else 'added'})", t["tax"]))
    rows.append(("TOTAL PAID", t["total"]))
    for label, value in rows:
        bold = label == "TOTAL PAID"
        d.text((W - MARGIN - 520, y), label, fill=GREEN if bold else INK, font=_font(30 if bold else 26))
        d.text((W - MARGIN - 160, y), value, fill=GREEN if bold else INK, font=_font(30 if bold else 26)); y += 46
    d.text((MARGIN, H - 140), "Thank you for travelling with WoDin. Your journey. Your memories. Your book.", fill=MUTED, font=_font(24))
    buf = io.BytesIO()
    img.save(buf, "PDF", resolution=150.0)
    return buf.getvalue()
