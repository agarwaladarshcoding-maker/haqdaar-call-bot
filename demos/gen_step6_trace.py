#!/usr/bin/env python3
"""Builds a JSON trace of the real menu.py functions (main_menu,
need_menu, schemes_for, section_text, resolve_code) so the visual demo
can let the user browse the number tree and dial codes directly, same
principle as gen_step5_trace.py: nothing hand-scripted, every value is a
real return from menu.py against the 20-scheme demo DB."""
import json
import os
import sys
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT + "/src")

DB = "/tmp/menu_viz_demo.db"
subprocess.run([sys.executable, ROOT + "/scripts/seed_demo.py", DB], check=True, capture_output=True)

from haqdaar import menu

menu._set_db_path_for_testing(DB)

out = {"main_menu": [], "need_menus": {}, "scheme_lists": {}, "sections": {}, "dial_codes": {}}

main = menu.main_menu()
out["main_menu"] = [{"key": o.key, "hi": o.label_hi, "en": o.label_en} for o in main]

for ps in main:
    needs = menu.need_menu(ps.key)
    out["need_menus"][ps.key] = [{"key": n.key, "hi": n.label_hi, "en": n.label_en} for n in needs]

    if needs:
        for n in needs:
            listings, has_more = menu.schemes_for(ps.key, need_key=n.key)
            out["scheme_lists"][f"{ps.key}:{n.key}"] = {
                "has_more": has_more,
                "items": [
                    {"slug": l.slug, "scheme_no": l.scheme_no, "name": l.name_short_hi or l.scheme_name, "verified": l.verified}
                    for l in listings
                ],
            }
    else:
        listings, has_more = menu.schemes_for(ps.key)
        out["scheme_lists"][f"{ps.key}:"] = {
            "has_more": has_more,
            "items": [
                {"slug": l.slug, "scheme_no": l.scheme_no, "name": l.name_short_hi or l.scheme_name, "verified": l.verified}
                for l in listings
            ],
        }

# Sections for every scheme referenced above
all_slugs = set()
for bucket in out["scheme_lists"].values():
    for item in bucket["items"]:
        all_slugs.add(item["slug"])

SEC_LABELS = {"1": ("kya milega", "what you get"), "2": ("kaun le sakta hai", "who is eligible"),
              "3": ("kaunse kaagaz chahiye", "documents needed"), "4": ("aavedan kaise kare", "how to apply"),
              "5": ("yojna ke baare mein", "about the scheme")}

for slug in all_slugs:
    out["sections"][slug] = {}
    for sec in "12345":
        text = menu.section_text(slug, sec)
        out["sections"][slug][sec] = text

# Dial code examples: a few valid ones spanning verified + unverified, plus
# a couple of invalid ones, all resolved for real via resolve_code().
import sqlite3
conn = sqlite3.connect(DB)
rows = conn.execute("SELECT scheme_no, verified FROM schemes ORDER BY scheme_no").fetchall()
sample_codes = []
for scheme_no, verified in rows[:6]:
    sample_codes.append(f"{scheme_no:02d}1")
for scheme_no, verified in rows:
    if verified == 0:
        sample_codes.append(f"{scheme_no:02d}4")
        break
sample_codes += ["999", "abc", "05"]

for code in sample_codes:
    result = menu.resolve_code(code)
    if result is None:
        out["dial_codes"][code] = None
    else:
        slug, sec_key = result
        out["dial_codes"][code] = {
            "slug": slug, "sec_key": sec_key,
            "sec_label_hi": SEC_LABELS[sec_key][0], "sec_label_en": SEC_LABELS[sec_key][1],
            "text": menu.section_text(slug, sec_key),
        }
        if slug not in out["sections"]:
            out["sections"][slug] = {s: menu.section_text(slug, s) for s in "12345"}

out["digit_words"] = {k: v for k, v in menu.DIGIT_WORDS_HI.items()}

out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step6_menu_trace.json")
with open(out_path, "w", encoding="utf-8") as f:
    f.write(json.dumps(out, ensure_ascii=False))
print("wrote", out_path, "| main menu items:", len(out["main_menu"]), "| sections:", len(out["sections"]))
