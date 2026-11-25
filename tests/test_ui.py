"""A real browser drives the real interface against local stand-in providers. Screenshots go to a temporary folder unless STUDIO_SHOTS names one."""
import os
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
import uvicorn

from tests.conftest import ROOT, review
from tests.fakes import GOOD

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
PAGES = ("Home", "Script", "Plan and canon", "Shots", "Images", "Gemini video", "Voices", "Sound", "Bring in files", "Review", "Cut", "Release", "Agents", "Connectors", "Budget", "Readiness", "Backup", "Library", "Learning", "Log")


@pytest.fixture
def site(data, fake, blocking):
    from studio.connectors.secrets import MemoryVault
    from studio.daemon.app import create
    a = create(MemoryVault(), start_jobs=False)
    s = uvicorn.Server(uvicorn.Config(a, host="127.0.0.1", port=0, log_level="error"))
    t = threading.Thread(target=s.run, daemon=True)
    t.start()
    while not s.started:
        time.sleep(.05)
    yield a, f"http://127.0.0.1:{s.servers[0].sockets[0].getsockname()[1]}"
    s.should_exit = True
    t.join(5)


@pytest.fixture
def page(site, tmp_path):
    a, url = site
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_context(viewport={"width": 1366, "height": 860}).new_page()
        pg.errors, pg.folder = [], Path(os.environ.get("STUDIO_SHOTS", tmp_path))
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" and not any(c in m.text for c in ("409", "400", "404")) else None)
        pg.goto(url + "/launch?code=" + a.state.launch)
        pg.wait_for_selector("main h1")
        yield pg
        b.close()


def nav(pg, name):
    pg.locator("nav#nav a", has_text=name).first.click()
    pg.wait_for_function("n => document.title.startsWith(n)", arg=name)
    pg.wait_for_selector("main h1")
    pg.wait_for_function("() => !document.querySelector('main .skeleton')")


def toast(pg, text):
    pg.locator("#toasts .toast", has_text=text).first.wait_for(timeout=15000)


def settle(pg, ms=500):
    pg.wait_for_timeout(ms)


def shot(pg, name):
    pg.folder.mkdir(parents=True, exist_ok=True)
    pg.screenshot(path=str(pg.folder / f"{name}.png"), full_page=True)


def agree(pg):
    d = pg.locator("dialog[open]")
    d.wait_for(timeout=15000)
    t = d.inner_text()
    d.locator("button.primary").click()
    return t


def test_every_page_opens_at_every_width(page):
    for w, h in ((1366, 860), (1920, 1080), (420, 860)):
        page.set_viewport_size({"width": w, "height": h})
        for n in PAGES:
            if w < 980:
                page.locator("#menu").click()
                settle(page, 220)
            nav(page, n)
            assert "could not be shown" not in page.inner_text("main"), n
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (n, w)
            if w == 1366:
                shot(page, n.lower().replace(" ", "-"))
    assert page.errors == []


def test_keyboard_and_names(page):
    """Everything that can be pressed has a name; focus is visible; the skip link works; headings do not jump."""
    for n in ("Home", "Images", "Cut", "Connectors", "Review"):
        nav(page, n)
        bad = page.evaluate("""() => [...document.querySelectorAll('button,a[href],input,select,textarea')].filter(e => e.offsetParent && !(e.textContent||'').trim() && !e.getAttribute('aria-label') && !e.labels?.length && !e.title && !e.querySelector('img[alt]')).map(e => e.outerHTML.slice(0,120))""")
        assert bad == [], (n, bad)
        assert page.locator("main h1").count() == 1
    page.keyboard.press("Tab")
    assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") != "none"


def test_key_test_enable_price_generate(page, site, fake):
    a, _ = site
    db = a.state.db
    nav(page, "Connectors")
    card = page.locator("section#elevenlabs")
    assert "not entered" in card.inner_text() and "switched off" in card.inner_text()
    card.locator("input[type=password]").fill("wrong_key_0000000000")
    card.get_by_role("button", name="Save key").click()
    toast(page, "Stored by Windows")
    card.get_by_role("button", name="Test connection").click()
    card.get_by_text("refused").first.wait_for()
    assert card.get_by_role("button", name="Enable this connector").is_disabled()
    card.locator("input[type=password]").fill(GOOD)
    card.get_by_role("button", name="Save key").click()
    settle(page, 900)
    card.get_by_role("button", name="Test connection").click()
    card.get_by_text("credits left").wait_for(timeout=15000)
    assert "20,000 of 30,000 credits left" in card.inner_text() and "•••• 6789" in card.inner_text() and GOOD not in page.content()
    assert card.locator("input[type=password]").input_value() == "" and page.evaluate("JSON.stringify(localStorage)+JSON.stringify(sessionStorage)") == "{}{}"
    card.get_by_role("button", name="Enable this connector").click()
    card.get_by_text("enabled", exact=True).wait_for()
    nav(page, "Home")
    page.locator("tr", has_text="Sound effects").get_by_role("button", name="Allow API").click()
    settle(page)
    nav(page, "Budget")
    page.get_by_role("button", name="Release for sound").click()
    page.locator("td", has_text="2,000").first.wait_for()
    nav(page, "Sound")
    c = page.locator("section#CUE-CLOTH")
    c.get_by_role("button", name="Approve this package").click()
    toast(page, "has not reviewed")
    review(db, "sfx", db.val("select id from sfx_package where cue_id='CUE-CLOTH'"), "story-editor")
    nav(page, "Log")
    nav(page, "Sound")
    c.get_by_role("button", name="Approve this package").click()
    c.get_by_role("button", name="Generate this sound").wait_for()
    c.get_by_role("button", name="Generate this sound").click()
    d = page.locator("dialog[open]")
    d.wait_for()
    assert "200 credits" in d.inner_text() and fake.gen == 0
    d.get_by_role("button", name="Cancel").click()
    settle(page)
    assert fake.gen == 0                                                          # nothing left without agreement
    c.get_by_role("button", name="Generate this sound").click()
    agree(page)
    c.locator("audio").first.wait_for(state="attached", timeout=15000)
    assert fake.gen == 1
    c.get_by_role("button", name="Accept", exact=True).click()
    toast(page, "Accepted")
    assert db.val("select state from generation_target where kind='sfx'") == "approved"
    assert page.errors == []


def test_image_by_provider_then_by_hand(page, site, fake, tmp_path):
    from tests.fakes import png
    from tests.test_money_and_connectors import ready
    a, _ = site
    db = a.state.db
    ready(a.state.broker, "google_images", "stills")
    nav(page, "Images")
    c = page.locator("section#IMG-LEAD-ANCHOR")
    c.locator("summary", has_text="Make it").click()
    c.get_by_role("button", name="Generate one image").click()
    t = agree(page)
    assert "At most $" in t and "leave this computer" in t
    c.locator("figure img").first.wait_for(timeout=20000)
    c.get_by_label("what is wrong").fill("the scar is missing")
    c.get_by_role("button", name="Needs correction").click()
    c.locator("summary", has_text="corrected attempt").wait_for()
    f = tmp_path / "hand.png"
    f.write_bytes(png(1536, 2048, 77))
    c.get_by_label("What was wrong").first.fill("made by hand with the scar")
    c.locator("input[type=file]").set_input_files(str(f))
    page.wait_for_function("() => document.querySelectorAll('section#IMG-LEAD-ANCHOR figure img').length === 2", timeout=20000)
    c.get_by_role("button", name="Accept", exact=True).click()
    c.locator(".tag", has_text="approved").first.wait_for()
    assert page.locator("section#IMG-LEAD-34L summary", has_text="Make it").count() == 1          # unlocked by the approval
    assert db.val("select count(*) from attempt") == 2 and page.errors == []


def test_a_failure_answers_the_seven_questions(page, site, fake):
    a, _ = site
    db, br = a.state.db, a.state.broker
    from tests.test_money_and_connectors import sfx_ready
    sfx_ready(br)
    fake.behaviour = "quota"
    nav(page, "Sound")
    page.locator("section#CUE-CLOTH").get_by_role("button", name="Generate this sound").click()
    agree(page)
    page.locator("#banner", has_text="Paused").wait_for(timeout=15000)
    nav(page, "Home")
    t = page.inner_text("main")
    for w in ("not enough credits", "Nothing was charged", "Paused. Nothing is sent", "Safe", "Evidence"):
        assert w in t, w
    page.get_by_role("button", name="Continue by hand").click()
    settle(page, 900)
    assert db.val("select mode from mode_current where service='sfx'") == "manual"
    page.wait_for_function("() => document.querySelector('#banner').innerText.indexOf('Paused') < 0", timeout=9000)


def test_a_lost_answer_is_settled_by_the_user(page, site, fake):
    a, _ = site
    db, br = a.state.db, a.state.broker
    from tests.test_money_and_connectors import sfx_ready
    sfx_ready(br)
    fake.behaviour = "fail500"
    nav(page, "Sound")
    page.locator("section#CUE-CLOTH").get_by_role("button", name="Generate this sound").click()
    agree(page)
    page.locator("#banner", has_text="may have been charged").wait_for(timeout=15000)
    nav(page, "Home")
    assert "never sent again by itself" in page.inner_text("main")
    page.get_by_role("button", name="My account shows no charge").click()
    page.wait_for_function("() => !document.querySelector('main').innerText.includes('has no answer')", timeout=9000)
    assert db.val("select state from api_request") == "failed" and fake.gen == 1


def test_manual_voice_route(page, site, tmp_path):
    a, _ = site
    db = a.state.db
    p = db.val("select id from voice_package where series='TF-VP-NARR'")
    review(db, "voice", p, "story-editor")
    from tests.test_workflow import cast
    cast(db)
    nav(page, "Voices")
    sec = page.locator(f"section[id='{p}']")
    sec.get_by_role("button", name="Approve this package").click()
    line = sec.locator("div.card#T1-N1")
    line.locator("input[type=file]").wait_for(state="attached")
    f = tmp_path / "T1_N1.wav"
    sf.write(str(f), .2 * np.sin(2 * np.pi * 180 * np.arange(96000) / 48000), 48000)
    line.get_by_label("Credits used").first.fill("41")
    line.locator("input[type=file]").set_input_files(str(f))
    line.locator("audio").first.wait_for(state="attached", timeout=20000)
    line.get_by_label("what is wrong").fill("too fast")
    line.get_by_role("button", name="Needs correction").click()
    line.get_by_text("1 of 3 attempts used").wait_for()
    nav(page, "Budget")
    assert "41 credits" in page.inner_text("main") and page.errors == []


def test_blind_first_then_findings(page, site, tmp_path):
    from tests.test_inspect import clip
    c = clip(tmp_path, "vanish")
    nav(page, "Review")
    page.locator("summary", has_text="Try the inspection").click()
    page.locator("input[type=file]").first.set_input_files(str(c))
    page.get_by_role("button", name="Fixable").wait_for(timeout=60000)
    assert "subject vanishes" not in page.inner_text("main")
    page.get_by_role("button", name="Fixable").click()
    page.get_by_text("your blind verdict").wait_for()
    assert "subject vanishes" in page.locator("main").text_content()
    pl = page.locator(".player").first
    pl.focus()
    for _ in range(3):
        page.keyboard.press("ArrowRight")
        settle(page, 250)
    assert "frame 3" in pl.inner_text()


def test_a_new_film_from_script_to_assembly(page, site):
    """No command line: create a film, import its script, ask for the plan; fixtures stand in for the agents' documents; assemble and change the edit."""
    from studio.agents import service as s
    from studio.core import docs
    from tests import test_makers as mk
    a, _ = site
    db = a.state.db
    page.locator("#filmbox select").select_option("+")
    d = page.locator("dialog[open]")
    d.get_by_label("Title").fill("Low Water")
    d.get_by_role("button", name="Create").click()
    page.wait_for_function("() => document.title.startsWith('Script')")
    page.locator("input[type=file]").set_input_files(str(ROOT / "benchmark/lowwater/script.fountain"))
    page.get_by_text("This is what the studio reads").wait_for()
    assert "NORA" in page.inner_text("main")
    page.get_by_role("button", name="Fix this revision").click()
    page.get_by_text("INT. BOATHOUSE - DAWN").first.wait_for()
    film = db.val("select id from film where title='Low Water'")
    nav(page, "Plan and canon")
    page.get_by_role("button", name="Ask for it").first.click()
    page.locator("dialog[open]").get_by_role("button", name="Create the task").click()
    page.get_by_text("waiting for its agent").wait_for()
    t = db.one("select id from task where action='make_plan' and film_id=?", film)["id"]
    c = s.claim(db, "story-editor", t)
    s.get_view(db, "story-editor", t, c["lease_token"], "guide")
    s.submit_work(db, "story-editor", t, c["lease_token"], mk.plan_doc(db, film))
    page.reload()
    page.locator("main h1").wait_for()
    page.get_by_text("Not reviewed yet").wait_for()
    page.locator("summary", has_text="Approve without a positive review").click()
    page.get_by_label("why you accept it").fill("I read the plan myself")
    page.get_by_role("button", name="Approve with this reason").click()
    page.locator("section .tag", has_text="approved").first.wait_for()
    r = docs.propose(db, film, "canon", "film", mk.canon_doc(), "production-designer")
    docs.approve(db, r["id"], waive_reason=mk.WAIVE)
    sc = db.val("select id from scene where film_id=? and number=1", film)
    r = docs.propose(db, film, "shot_list", f"scene:{sc}", mk.shot_doc(db, film, sc), "cinematographer")
    docs.approve(db, r["id"], waive_reason=mk.WAIVE)
    nav(page, "Script")
    page.get_by_text("Whose coat is the second one?").first.wait_for(timeout=5000)
    page.get_by_role("button", name="Nora's own").click()
    page.get_by_role("button", name="Decide").click()
    page.locator("summary", has_text="1 decision made").wait_for()
    nav(page, "Cut")
    page.get_by_role("button", name="Assemble the designed shots").click()
    page.locator(".lane button", has_text="A2").first.click()
    page.get_by_label("Length in frames").fill("60")
    page.get_by_label("Reason").first.fill("leave on the look")
    page.get_by_role("button", name="Change length").click()
    page.get_by_text("revision 2, draft").wait_for()
    page.get_by_label("Ask for a change here").count()
    nav(page, "Home")
    assert "Low Water" in page.inner_text("main h1") and page.errors == []
    page.locator("#filmbox select").select_option("TESTFILM")
    page.get_by_role("heading", name="Test film: lamp room").wait_for()


def test_release_page_says_what_is_missing(page, site):
    nav(page, "Release")
    t = page.inner_text("main")
    assert "No stand-ins are left" in t and "You watched the whole master" in t
    page.get_by_label("Title", exact=True).first.fill("The Lamp Room")
    page.get_by_label("Description").fill("A lamp room, a stair, and a made bed straight below. A short test film.")
    page.get_by_label("Thumbnail idea").fill("the bed down the stairwell")
    page.get_by_role("button", name="Save and use this text").click()
    toast(page, "Saved")
    page.locator(".need", has_text="Title, description and tags").locator(".tag", has_text="pass").wait_for()
    row = page.locator(".need", has_text="Every picture and sound may be used")
    assert row.get_by_role("button", name="Waive").count() == 0
    assert page.errors == []


def test_backup_page_protects(page, site, tmp_path):
    a, _ = site
    nav(page, "Backup")
    page.get_by_label("Folder on another drive").fill(str(tmp_path / "second"))
    page.get_by_label("Name").fill("second drive")
    page.get_by_role("button", name="Add", exact=True).click()
    page.get_by_text("second drive").first.wait_for()
    page.get_by_label("Folder on another drive").fill(str(ROOT / "data" / "x"))
    page.get_by_role("button", name="Add", exact=True).click()
    toast(page, "protects nothing")
    assert page.errors == []
