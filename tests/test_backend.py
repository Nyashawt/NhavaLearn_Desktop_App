"""Headless test of the NhavaLearn backend (everything except the webview windows)."""
import os, sys, tempfile, types
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp()
# stub webview module so app.api imports without a GUI
sys.modules["webview"] = types.SimpleNamespace(screens=[], create_window=None)
sys.path.insert(0, "/home/claude/nhavalearn")

from app import db
from app.api import Api

db.init_db()
api = Api()

def ok(r, label):
    assert r.get("ok"), f"{label} FAILED: {r}"
    print(f"  ok: {label}")

def fail(r, label):
    assert not r.get("ok"), f"{label} should have failed but passed"
    print(f"  ok (correctly refused): {label} -> {r['error']}")

print("== setup ==")
assert not db.setup_complete()
fail(api.complete_setup("", "", "", "", ""), "empty setup")
ok(api.complete_setup("Nhava Primary", "Murewa", "Mrs Moyo", "tmoyo", "secret1"), "wizard")
fail(api.complete_setup("X", "", "A", "a", "secret1"), "second setup")

print("== auth ==")
fail(api.login("tmoyo", "wrong"), "wrong password")
ok(api.login("tmoyo", "secret1"), "admin login")

print("== admin creates accounts ==")
ok(api.create_user("Mr Ncube", "bncube", "secret1", "teacher"), "create teacher 1")
ok(api.create_user("Ms Dube", "sdube", "secret1", "teacher"), "create teacher 2")
ok(api.create_user("Head Master", "head", "secret1", "supervisor"), "create supervisor")
fail(api.create_user("Dup", "bncube", "secret1", "teacher"), "duplicate username")
fail(api.create_class("Grade 1"), "admin creating class (teacher-only)")

print("== teacher 1 workflow ==")
ok(api.login("bncube", "secret1"), "teacher 1 login")
r = api.create_class("Grade 6 Blue"); ok(r, "create class"); c1 = r["id"]
r = api.create_lesson(c1, "Fractions", 1); ok(r, "create lesson"); l1 = r["id"]
r = api.get_lesson(l1); ok(r, "get lesson"); p1 = r["pages"][0]["id"]
ok(api.save_page(p1, "Intro", "<p>Half is $$\\frac{1}{2}$$</p>"), "save page")
r = api.add_page(l1); ok(r, "add page"); assert r["page_number"] == 2
ok(api.delete_page(r["id"]), "delete page")
fail(api.delete_page(p1), "delete last remaining page")

print("== ownership boundaries ==")
ok(api.login("sdube", "secret1"), "teacher 2 login")
fail(api.get_lesson(l1), "teacher 2 opening teacher 1's lesson")
fail(api.create_lesson(c1, "Sneaky", None), "teacher 2 adding to teacher 1's class")
fail(api.delete_class(c1), "teacher 2 deleting teacher 1's class")
r = api.list_classes(); ok(r, "teacher 2 list_classes"); assert len(r["classes"]) == 0, "teacher 2 sees only own"

print("== supervisor oversight ==")
ok(api.login("head", "secret1"), "supervisor login")
r = api.list_classes(); ok(r, "supervisor sees all classes"); assert len(r["classes"]) == 1
r = api.get_lesson(l1); ok(r, "supervisor reads any lesson")
fail(api.save_page(p1, "x", "y"), "supervisor editing (read-only)")
fail(api.create_user("X", "x", "secret1", "teacher"), "supervisor creating account")
r = api.list_users(); ok(r, "supervisor lists accounts")

print("== dashboard & settings ==")
r = api.get_dashboard(); ok(r, "supervisor dashboard"); assert r["stats"]["teachers"] == 2
ok(api.login("tmoyo", "secret1"), "admin re-login")
ok(api.update_settings("Nhava Primary School", "Murewa"), "update settings")
ok(api.create_subject("Music"), "add subject")
fail(api.set_user_active(1, False), "admin deactivating self")
ok(api.set_user_active(3, False), "deactivate teacher 2")
api.logout()
fail(api.login("sdube", "secret1"), "deactivated user login")



print("== reorder & video permissions ==")
ok(api.login("bncube", "secret1"), "teacher 1 re-login")
r = api.get_lesson(l1); ok(r, "reload lesson")
pa = r["pages"][0]["id"]
r2 = api.add_page(l1); ok(r2, "add page for reorder"); pb = r2["id"]
ok(api.reorder_pages(l1, [pb, pa]), "reorder pages")
r = api.get_lesson(l1)
assert [p["id"] for p in r["pages"]] == [pb, pa], "reorder persisted"
print("  ok: reorder persisted in page_number order")
r = api.attach_video(999999)
fail(r, "attach video to someone else's / missing lesson")
api.logout()
fail(api.attach_video(l1), "attach video signed out")
print("\nEXTENDED TESTS PASSED")

print("== bridge safety ==")
# Public data attributes on Api get crawled by pywebview's JS-bridge builder;
# a window object here caused infinite recursion (the .Bounds.Empty bug).
bad = [a for a in vars(api) if not a.startswith("_")]
assert not bad, f"public attrs would leak into the JS bridge: {bad}"
print("  ok: Api exposes methods only — no public data attributes")
print("\nALL TESTS PASSED")

print("== simulation library ==")
ok(api.login("tmoyo", "secret1"), "admin login for sims")
fail(api.phet_download("Evil", "https://evil.example/x.html"), "download from non-PhET url shape")
fail(api.phet_download("Evil2", "/sims/html/../../etc/passwd"), "download path not ending .html")
# seed a sim row directly (file install needs a dialog; catalog needs internet)
import shutil as _sh
_simfile = os.path.join(db.data_dir(), "media", "sim_test_balloons.html")
open(_simfile, "w").write("<html><body>sim</body></html>")
conn = db.connect()
conn.execute("INSERT INTO sims (title, kind, filename, source) VALUES ('Balloons', 'phet', 'sim_test_balloons.html', '/sims/html/x_en.html')")
conn.commit(); conn.close()
r = api.list_sims(); ok(r, "admin lists sims"); sim_id = r["sims"][0]["id"]
fail(api.add_sim_page(l1, sim_id), "admin adding sim page (teacher-only)")

ok(api.login("bncube", "secret1"), "teacher login for sims")
r = api.list_sims(); ok(r, "teacher sees library")
fail(api.delete_sim(sim_id), "teacher deleting sim (admin-only)")
r = api.add_sim_page(l1, sim_id); ok(r, "teacher adds sim page")
assert r["sim_path"] == "sim_test_balloons.html" and r["title"] == "Balloons"
lesson = api.get_lesson(l1)
simpage = [p for p in lesson["pages"] if p["page_type"] == "simulation"]
assert len(simpage) == 1 and simpage[0]["sim_path"] == "sim_test_balloons.html"
print("  ok: sim page persisted with type and path")
fail(api.add_sim_page(l1, 99999), "adding missing sim")

ok(api.login("tmoyo", "secret1"), "admin re-login")
fail(api.delete_sim(sim_id), "deleting a sim still used by a lesson page")
conn = db.connect(); conn.execute("DELETE FROM lesson_pages WHERE page_type='simulation'"); conn.commit(); conn.close()
ok(api.delete_sim(sim_id), "deleting unused sim")
assert not os.path.exists(_simfile), "sim file removed from disk"
print("  ok: sim file cleaned up on delete")

print("\nSIM LIBRARY TESTS PASSED")

print("== GeoGebra runtime ==")
import io, zipfile as _zf, urllib.request as _ur
from app import media_server as _ms

r = api.geogebra_status(); ok(r, "status check"); assert r["installed"] is False
print("  ok: runtime reported not installed initially")

# viewer route returns the friendly 'not installed' page
_base = _ms.ggb_base_url()
body = _ur.urlopen(_base + "test.ggb").read().decode()
assert "runtime not installed" in body.lower()
print("  ok: /ggb/ route serves friendly message when runtime missing")

# a zip with path traversal is rejected outright
_evil = os.path.join(db.data_dir(), "evil.zip")
with _zf.ZipFile(_evil, "w") as z:
    z.writestr("../outside.txt", "bad")
    z.writestr("GeoGebra/deployggb.js", "// deploy")
fail(api._extract_ggb_runtime(_evil), "zip with path traversal")
assert not os.path.exists(os.path.join(db.data_dir(), "media", "outside.txt"))
print("  ok: traversal file never written")

# a zip without deployggb.js is rejected
_notggb = os.path.join(db.data_dir(), "notggb.zip")
with _zf.ZipFile(_notggb, "w") as z:
    z.writestr("readme.txt", "hi")
fail(api._extract_ggb_runtime(_notggb), "zip without deployggb.js")

# a synthetic Math-Apps-Bundle-shaped zip installs cleanly
_bundle = os.path.join(db.data_dir(), "bundle.zip")
with _zf.ZipFile(_bundle, "w") as z:
    z.writestr("GeoGebra/deployggb.js", "// GGBApplet stub")
    z.writestr("GeoGebra/HTML5/5.0/web3d/web3d.nocache.js", "// codebase stub")
ok(api._extract_ggb_runtime(_bundle), "install synthetic runtime bundle")
r = api.geogebra_status(); assert r["installed"] is True
print("  ok: runtime detected after install")

# viewer route now serves a real applet page, same-origin paths throughout
body = _ur.urlopen(_base + "circle.ggb").read().decode()
assert "deployggb.js" in body and "/media/geogebra/GeoGebra/deployggb.js" in body
assert 'filename: "/media/circle.ggb"' in body
assert "setHTML5Codebase" in body and "HTML5/5.0/web3d/" in body
print("  ok: /ggb/ viewer boots runtime with correct same-origin paths")

# nested runtime files are reachable through /media/
body = _ur.urlopen(_ms.base_url() + "geogebra/GeoGebra/deployggb.js").read().decode()
assert "GGBApplet stub" in body
print("  ok: nested runtime files served over /media/")

# reinstall replaces cleanly
ok(api._extract_ggb_runtime(_bundle), "reinstall over existing runtime")

# ggb_ready surfaces in list_sims
r = api.list_sims(); ok(r, "list_sims includes ggb readiness"); assert r["ggb_ready"] is True

print("\nGEOGEBRA TESTS PASSED")

print("== test management ==")
ok(api.login("bncube", "secret1"), "teacher login for tests")
r = api.create_test(c1, "Fractions end-of-topic", 1); ok(r, "create test"); t1 = r["id"]
fail(api.create_test(c1, "", 1), "test with empty title")
r = api.get_test(t1); ok(r, "get test"); q1 = r["questions"][0]["id"]
assert len(r["questions"]) == 1, "new test starts with one question"

ok(api.save_question(q1, "multiple_choice", "What is 1/2 + 1/4?",
   ["1/2", "3/4", "2/6", "1/8"], "1", "Common denominator is 4.", 2), "save MC question")
r = api.add_question(t1, "true_false"); ok(r, "add TF question"); q2 = r["id"]
ok(api.save_question(q2, "true_false", "3/6 equals 1/2.", [], "true", "", 1), "save TF question")
r = api.add_question(t1, "short_answer"); ok(r, "add short answer"); q3 = r["id"]
ok(api.save_question(q3, "short_answer", "Write 0.75 as a fraction.", [], "3/4", "", 2), "save short answer")
fail(api.add_question(t1, "essay"), "invalid question kind")

ok(api.reorder_questions(t1, [q3, q1, q2]), "reorder questions")
r = api.get_test(t1)
assert [q["id"] for q in r["questions"]] == [q3, q1, q2], "reorder persisted"
print("  ok: question order persisted")

ok(api.update_test(t1, "Fractions Test A", "Answer all questions."), "update test meta")

r = api.test_pages(t1, False); ok(r, "generate question-only pages")
assert len(r["pages"]) == 4, "cover + 3 questions"
cover = r["pages"][0]["content_html"]
assert "5</b> marks total" in cover and "3</b> question" in cover, cover
joined = "".join(p["content_html"] for p in r["pages"])
assert "3/4" in joined and "&#10003;" not in joined and "Why:" not in joined
print("  ok: question-only pages hide answers")

r = api.test_pages(t1, True); ok(r, "generate review pages")
joined = "".join(p["content_html"] for p in r["pages"])
assert "&#10003;" in joined and "Common denominator" in joined and "Answer: TRUE" in joined
print("  ok: review pages reveal answers + explanations")

# XSS: prompts are escaped in generated pages
qx = api.add_question(t1, "short_answer")["id"]
ok(api.save_question(qx, "short_answer", "<script>alert(1)</script>", [], "", "", 1), "save hostile prompt")
joined = "".join(p["content_html"] for p in api.test_pages(t1, False)["pages"])
assert "<script>" not in joined and "&lt;script&gt;" in joined
print("  ok: prompts escaped in generated pages")
ok(api.delete_question(qx), "delete question")

print("== test permissions ==")
ok(api.login("tmoyo", "secret1"), "admin reactivates teacher 2")
ok(api.set_user_active(3, True), "reactivate sdube")
ok(api.login("sdube", "secret1"), "teacher 2 login")
fail(api.get_test(t1), "teacher 2 opening teacher 1's test")
fail(api.save_question(q1, "true_false", "x", [], "true", "", 1), "teacher 2 editing question")
fail(api.delete_test(t1), "teacher 2 deleting test")
ok(api.login("head", "secret1"), "supervisor login")
r = api.get_test(t1); ok(r, "supervisor reads test with key")
r = api.test_pages(t1, True); ok(r, "supervisor generates review pages")
fail(api.update_test(t1, "x", ""), "supervisor editing test")
ok(api.login("tmoyo", "secret1"), "admin login")
fail(api.create_test(c1, "Admin test", None), "admin creating test (teacher-only)")
r = api.get_dashboard(); ok(r, "admin dashboard still works")

print("\nTEST MANAGEMENT TESTS PASSED")

print("== one-pager layout ==")
ok(api.login("bncube", "secret1"), "teacher login for layout")
ok(api.update_test(t1, "Fractions Test A", "Answer all questions.", "single"), "set single layout")
r = api.test_pages(t1, False); ok(r, "generate one-pager")
assert len(r["pages"]) == 1, "single layout yields exactly one page"
body = r["pages"][0]["content_html"]
assert "Question 1" in body and "Question 3" in body and "marks total" in body
print("  ok: one page contains all questions + cover info")
ok(api.update_test(t1, "Fractions Test A", "Answer all questions.", "bogus"), "bogus layout ignored, meta still saves")
r = api.get_test(t1); assert r["test"]["layout"] == "single", "bogus layout didn't overwrite"
print("  ok: invalid layout values ignored")
ok(api.update_test(t1, "Fractions Test A", "Answer all questions.", "pages"), "back to per-question pages")
r = api.test_pages(t1, False); assert len(r["pages"]) == 4
print("  ok: pages layout restored")

print("\nONE-PAGER TESTS PASSED — ALL SUITES GREEN")

print("== AI generation ==")
ok(api.login("bncube", "secret1"), "teacher login for AI")
r = api.ai_get_status(); ok(r, "ai status check")
fail(api.ai_generate_and_create(c1, "test", 1, "Fractions", 5), "generate with no provider configured")
fail(api.ai_generate_and_create(c1, "bogus", 1, "Fractions"), "invalid kind rejected")
fail(api.ai_generate_and_create(c1, "test", 1, ""), "empty topic rejected")
ok(api.login("tmoyo", "secret1"), "admin login for AI settings")
ok(api.update_ai_settings("cloud", "sk-test-fake-key", None), "admin sets API key")
r = api.get_ai_settings(); ok(r, "admin reads AI settings"); assert r["settings"]["api_key_set"] is True
ok(api.login("sdube", "secret1"), "teacher 2 login")
fail(api.update_ai_settings("cloud", "x", None), "teacher updating AI settings (admin-only)")
fail(api.ai_generate_and_create(c1, "test", 1, "Fractions"), "teacher 2 generating for teacher 1's class")

print("\nAI GENERATION TESTS PASSED")

print("== library documents ==")
ok(api.login("tmoyo", "secret1"), "admin login for documents")
fail(api.upload_document("Notes", 1, "Grade 6"), "upload with no file dialog available (headless)")
fail(api.upload_document("Notes", 1, "Not A Grade"), "invalid grade rejected")

# seed a document directly (file upload needs a real dialog — mirrors the sim
# library test's approach of inserting the row and letting the trigger index it)
conn = db.connect()
conn.execute(
    "INSERT INTO documents (title, subject_id, grade, filename, original_name, extracted_text) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    ("Grade 6 Maths Notes", 1, "Grade 6", "doc_test.pdf", "notes.pdf",
     "Fractions are parts of a whole. Adding fractions with the same denominator "
     "means adding the numerators and keeping the denominator."),
)
conn.commit(); conn.close()

r = api.list_documents(); ok(r, "list documents"); assert len(r["documents"]) == 1
doc_id = r["documents"][0]["id"]
print("  ok: seeded document listed")

from app import documents as documents_mod
hits = documents_mod.search_documents("Grade 6", 1, "fractions")
assert hits and "fraction" in hits[0].lower()
print("  ok: FTS search finds a relevant excerpt")

hits2 = documents_mod.search_documents("Grade 6", 1, "photosynthesis")
assert hits2 == [], "unrelated topic should return no excerpts"
print("  ok: FTS search returns nothing for an unrelated topic")

hits3 = documents_mod.search_documents("Grade 7", 1, "fractions")
assert hits3 == [], "wrong grade should not match even with matching keywords"
print("  ok: FTS search respects grade scoping")

ok(api.login("bncube", "secret1"), "teacher login for document delete check")
fail(api.delete_document(doc_id), "teacher deleting document (admin-only)")
ok(api.login("tmoyo", "secret1"), "admin re-login")
ok(api.delete_document(doc_id), "admin deletes document")
r = api.list_documents(); assert len(r["documents"]) == 0
print("  ok: document removed")

print("\nLIBRARY DOCUMENTS TESTS PASSED")

print("== grade scoping ==")
ok(api.login("bncube", "secret1"), "teacher login for grade")
r = api.create_class("Grade 7 Green", "Grade 7"); ok(r, "create class with grade")
fail(api.create_class("Bad grade class", "Not A Grade"), "invalid grade rejected")
r = api.list_grades(); ok(r, "list grades"); assert "Form 4" in r["grades"]
print("  ok: grade validated against the fixed list")

print("\nALL EXTENDED TESTS PASSED")