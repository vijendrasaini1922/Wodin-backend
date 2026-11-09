import os, sys, io, random
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings"); os.environ["AI_PROVIDER"] = "mock"
import django; django.setup()
from datetime import date
from PIL import Image, ImageDraw
from apps.aidraft.planner import PhotoInfo, build_book, sanitise_story
from apps.aidraft.providers import MockProvider, ImageInput
from apps.aidraft.services import _merge
from apps.aibook import render
size = sys.argv[1]; N = int(sys.argv[2]); out = "/Users/vijendrasaini/Desktop/wodin/.bob/tmp"
random.seed(5); imgs, infos = {}, []
for i in range(N):
    land = i % 3 != 0; w, h = (2400, 1600) if land else (1600, 2400)
    im = Image.new("RGB", (w, h)); d = ImageDraw.Draw(im)
    c1 = tuple(random.randint(40, 200) for _ in range(3)); c2 = tuple(random.randint(40, 200) for _ in range(3))
    for y in range(0, h, 6): d.rectangle([0, y, w, y + 6], fill=tuple(int(a + (b - a) * y / h) for a, b in zip(c1, c2)))
    d.rectangle([w*.3, h*.15, w*.7, h*.9], outline=(255,255,255), width=8)   # the subject box
    d.ellipse([w*.42, h*.17, w*.58, h*.17+w*.16], fill=(250, 220, 190))      # a "head"
    d.rectangle([w*.38, h*.17+w*.17, w*.62, h*.9], fill=(60, 90, 140))       # a "body"
    imgs[f"p{i}"] = im; infos.append(PhotoInfo(id=f"p{i}", width=w, height=h, flags=[], taken=date(2025, 5, 1 + i // 4)))
res = MockProvider().plan_story([ImageInput(b"x", "")] * N, {"title": "Manali", "target_scenes": max(3, N // 2), "premium": True})
story = _merge([res.story], [N]); story["facts"].update(places=["Manali", "Solang"], travellers=["Aarav", "Meera"], dates="12-18 May 2025")
sizes, nums, k = [4, 5, 3, 5, 1, 4, 2], [], 1
scs = []
for z in sizes:
    ph = list(range(k, min(k + z, N + 1))); k += z
    if ph: scs.append({"label": f"Day {len(scs)+1}", "title": f"Chapter {len(scs)+1}", "story": "Snow, laughter and a long road home.", "footer": "Together", "hero": ph[0], "photos": ph})
story["scenes"] = scs
asp = {"portrait": 210 / 297, "square": 1.0, "landscape": 297 / 210}[size]
trip = {"title": "Manali Diaries", "cover_title": "Manali Diaries", "destinations": ["Manali", "Solang"], "companions": ["Aarav", "Meera"], "dates_text": "12-18 May 2025", "special_message": "", "dedication": ""}
pages, summ = build_book(infos, story, int(sys.argv[3]) if len(sys.argv) > 3 else 20, trip, asp, premium=True)
print(len(pages), [p["layout"] for p in pages], summ["quotes_used"], summ["map_stops"], summ["warnings"])
tw, th = {"portrait": (210, 297), "square": (210, 210), "landscape": (297, 210)}[size]; dpi = 90; px = lambda mm: round(mm / 25.4 * dpi)
r = render.PageRenderer(px(tw), px(th), px(3), lambda pid: imgs.get(pid))
import glob
for f in glob.glob(f"{out}/{size}_*.jpg"): os.remove(f)
for i, p in enumerate(pages):
    im = r.render(p); im.crop((px(3), px(3), px(3) + px(tw), px(3) + px(th))).save(f"{out}/{size}_{i:02d}.jpg", quality=82)
