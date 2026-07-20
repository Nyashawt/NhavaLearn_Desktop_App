/* NhavaLearn Desktop — frontend application.
   Vanilla JS single-page app. All data access goes through window.pywebview.api;
   role checks in the UI are cosmetic only — the Python bridge is the enforcer. */

"use strict";

const $app = document.getElementById("app");
let USER = null;           // current user (mirror of Python session)
let quill = null;          // active Quill instance in the editor view
let presenter = null;      // { lessonId, index, count } while presenting
let MEDIA_BASE = "";       // media server base URL, fetched at boot

/* ================================ utils ================================ */

const api = () => window.pywebview.api;

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function toast(msg) {
  let t = document.getElementById("toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "toast";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), 2400);
}

function renderMath(el) {
  if (window.renderMathInElement) {
    renderMathInElement(el, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "\\(", right: "\\)", display: false },
      ],
      throwOnError: false,
    });
  }
}

function modal(innerHtml, onMount) {
  const bd = document.createElement("div");
  bd.className = "modal-backdrop";
  bd.innerHTML = `<div class="modal">${innerHtml}</div>`;
  bd.addEventListener("click", e => { if (e.target === bd) bd.remove(); });
  document.body.appendChild(bd);
  if (onMount) onMount(bd);
  return bd;
}

/* =============================== router ================================ */

const routes = {};
function go(route, ...args) {
  quill = null; // drop editor instance when leaving
  routes[route](...args);
}

/* ------------------- Quill extensions (registered once) ------------------- */

Quill.register("modules/blotFormatter2", QuillBlotFormatter2.default);

/* Local video embed: value is the FILENAME in the app's media library.
   The src is rebuilt from the media server's current base URL on every load
   (the port is ephemeral), keyed off data-filename. */
const BlockEmbed = Quill.import("blots/block/embed");
class LocalVideo extends BlockEmbed {
  static blotName = "localvideo";
  static tagName = "video";
  static create(filename) {
    const node = super.create();
    node.dataset.filename = filename;
    node.setAttribute("src", MEDIA_BASE + encodeURIComponent(filename));
    node.setAttribute("controls", "");
    node.setAttribute("preload", "metadata");
    node.style.maxWidth = "100%";
    return node;
  }
  static value(node) { return node.dataset.filename || ""; }
}
Quill.register(LocalVideo);

/* Simulation URL: .ggb files open through the media server's /ggb/ viewer
   (which boots the GeoGebra runtime); everything else serves directly. */
function simSrc(path) {
  return path.toLowerCase().endsWith(".ggb")
    ? MEDIA_BASE.replace(/media\/$/, "ggb/") + encodeURIComponent(path)
    : MEDIA_BASE + encodeURIComponent(path);
}

/* Rewrite every embedded video's src against the current media base —
   heals lessons saved under a previous run's port. */
function fixVideoSrcs(rootEl) {
  rootEl.querySelectorAll("video[data-filename]").forEach(v => {
    v.setAttribute("controls", "");
    v.src = MEDIA_BASE + encodeURIComponent(v.dataset.filename);
  });
}

/* ============================ boot sequence ============================ */

window.addEventListener("pywebviewready", async () => {
  const mb = await api().get_media_base();
  MEDIA_BASE = mb.base;
  const state = await api().get_app_state();
  if (!state.setup_complete) go("setup");
  else go("login");
});

/* ============================ setup wizard ============================= */

routes.setup = () => {
  $app.innerHTML = `
  <div class="split-screen">
    <div class="split-hero">
      <div class="big">Welcome to <span class="gold">NhavaLearn</span></div>
      <p>Set up this device for your school. This only happens once — after setup, teachers sign in and start creating lessons.</p>
      <p><i class="bi bi-sun"></i> Works fully offline, powered by your SmartClass kit.</p>
    </div>
    <div class="split-form">
      <h2>School setup</h2>
      <p class="sub" style="color:var(--muted)">Step 1 of 1 — takes about a minute</p>
      <div id="err"></div>
      <label class="field">School name
        <input id="f-school" placeholder="e.g. Nhava Primary School" autofocus></label>
      <label class="field">Location <span style="font-weight:400">(optional)</span>
        <input id="f-location" placeholder="e.g. Murewa, Mashonaland East"></label>
      <hr style="border:none;border-top:1px solid var(--border);margin:18px 0">
      <h3 style="font-size:15px">Administrator account</h3>
      <label class="field">Full name
        <input id="f-name" placeholder="e.g. Mrs T. Moyo"></label>
      <label class="field">Username
        <input id="f-user" placeholder="e.g. tmoyo"></label>
      <label class="field">Password <span style="font-weight:400">(at least 6 characters)</span>
        <input id="f-pass" type="password"></label>
      <button class="btn" id="f-go"><i class="bi bi-check-lg"></i> Finish setup</button>
    </div>
  </div>`;

  document.getElementById("f-go").onclick = async () => {
    const v = id => document.getElementById(id).value;
    const r = await api().complete_setup(v("f-school"), v("f-location"), v("f-name"), v("f-user"), v("f-pass"));
    if (!r.ok) {
      document.getElementById("err").innerHTML = `<div class="error-msg">${esc(r.error)}</div>`;
      return;
    }
    toast("Setup complete — sign in to begin");
    go("login");
  };
};

/* ================================ login ================================ */

routes.login = async () => {
  const s = await api().get_settings();
  const school = s.settings ? s.settings.school_name : "";
  $app.innerHTML = `
  <div class="split-screen">
    <div class="split-hero">
      <div class="big">${esc(school) || "NhavaLearn"}</div>
      <p>Create, manage and present lessons — <span style="color:var(--gold);font-weight:700">no internet needed</span>.</p>
    </div>
    <div class="split-form">
      <h2>Sign in</h2>
      <div id="err"></div>
      <label class="field">Username <input id="l-user" autofocus></label>
      <label class="field">Password <input id="l-pass" type="password"></label>
      <button class="btn" id="l-go"><i class="bi bi-box-arrow-in-right"></i> Sign in</button>
    </div>
  </div>`;

  const submit = async () => {
    const r = await api().login(
      document.getElementById("l-user").value,
      document.getElementById("l-pass").value);
    if (!r.ok) {
      document.getElementById("err").innerHTML = `<div class="error-msg">${esc(r.error)}</div>`;
      return;
    }
    USER = r.user;
    go("dashboard");
  };
  document.getElementById("l-go").onclick = submit;
  document.getElementById("l-pass").addEventListener("keydown", e => { if (e.key === "Enter") submit(); });
};

/* ============================= app frame =============================== */

function frame(active, contentHtml) {
  const links = [["dashboard", "bi-house", "Home"], ["classes", "bi-collection", "Classes"]];
  if (USER.role === "admin") {
    links.push(["sims", "bi-joystick", "Simulations"],
               ["users", "bi-people", "Accounts"], ["settings", "bi-gear", "Settings"],
               ["aiSettings", "bi-stars", "AI Settings"],
               ["documents", "bi-file-earmark-text", "Library Documents"]);
  }
  if (USER.role === "supervisor") {
    links.push(["users", "bi-people", "Accounts"]);
  }
  $app.innerHTML = `
  <div class="frame">
    <aside class="sidebar">
      <div class="brand"><i class="bi bi-easel2"></i> NhavaLearn</div>
      <nav>
        ${links.map(([r, ic, lbl]) =>
          `<a href="#" data-route="${r}" class="${r === active ? "active" : ""}"><i class="bi ${ic}"></i> ${lbl}</a>`).join("")}
      </nav>
      <div class="whoami">
        <div class="name">${esc(USER.full_name)}</div>
        <div class="role">${esc(USER.role)}</div>
        <a href="#" id="logout" style="color:#94A3B8;font-size:13px"><i class="bi bi-box-arrow-left"></i> Sign out</a>
      </div>
    </aside>
    <div class="main">${contentHtml}</div>
  </div>`;

  $app.querySelectorAll("[data-route]").forEach(a =>
    a.onclick = e => { e.preventDefault(); go(a.dataset.route); });
  document.getElementById("logout").onclick = async e => {
    e.preventDefault();
    await api().logout();
    USER = null;
    go("login");
  };
}

/* ============================== dashboard ============================== */

routes.dashboard = async () => {
  const d = await api().get_dashboard();
  const tiles = USER.role === "teacher"
    ? [["classes", d.stats.classes, "My classes", "bi-collection", "tint-blue"],
       ["classes", d.stats.lessons, "My lessons", "bi-journal-text", "tint-purple"],
       ["classes", d.stats.tests, "My tests", "bi-clipboard-check", "tint-cyan"]]
    : [["users", d.stats.teachers, "Teachers", "bi-people", "tint-blue"],
       ["classes", d.stats.classes, "Classes", "bi-collection", "tint-purple"],
       ["classes", d.stats.lessons, "Lessons", "bi-journal-text", "tint-cyan"]];

  frame("dashboard", `
    <div class="hero-banner">
      <h2 style="font-weight:800">Welcome back, ${esc(USER.full_name.split(" ")[0])}</h2>
      <p style="margin:0;color:rgba(255,255,255,.85)">Teaching at <span class="school">${esc(d.school_name)}</span></p>
    </div>
    <div class="card-grid">
      ${tiles.map(([r, n, lbl, ic, tint]) => `
        <div class="card stat-tile ${tint} item-card" data-route="${r}">
          <div><div class="num">${n}</div><div class="lbl">${lbl}</div></div>
          <div class="icon"><i class="bi ${ic}"></i></div>
        </div>`).join("")}
    </div>
    ${USER.role === "teacher" ? `
      <div style="margin-top:24px">
        <button class="btn" data-route="classes"><i class="bi bi-plus-lg"></i> Go to my classes</button>
      </div>` : ""}
  `);
  $app.querySelectorAll(".main [data-route]").forEach(el =>
    el.onclick = () => go(el.dataset.route));
};

/* =============================== classes =============================== */

routes.classes = async () => {
  const r = await api().list_classes();
  const isTeacher = USER.role === "teacher";
  const cards = r.classes.length
    ? `<div class="card-grid">${r.classes.map(c => `
        <div class="card item-card" data-id="${c.id}">
          <div>
            <h3 style="font-size:16px">${esc(c.name)}</h3>
            <div class="meta">${isTeacher ? "" : esc(c.teacher_name) + " · "}${c.lesson_count} lesson${c.lesson_count === 1 ? "" : "s"}</div>
          </div>
          <i class="bi bi-chevron-right" style="color:var(--muted)"></i>
        </div>`).join("")}</div>`
    : `<div class="card empty-state"><i class="bi bi-collection"></i>
        ${isTeacher ? "No classes yet. Create your first class to start adding lessons." : "No classes have been created on this device yet."}</div>`;

  frame("classes", `
    <div class="page-head">
      <div><h2>${isTeacher ? "My classes" : "All classes"}</h2>
        <div class="sub">${isTeacher ? "Classes you teach on this device" : "Read-only view across every teacher"}</div></div>
      ${isTeacher ? `<button class="btn" id="new-class"><i class="bi bi-plus-lg"></i> New class</button>` : ""}
    </div>
    ${cards}`);

  $app.querySelectorAll(".item-card[data-id]").forEach(el =>
    el.onclick = () => go("lessons", Number(el.dataset.id)));

  const nb = document.getElementById("new-class");
  if (nb) nb.onclick = async () => {
    const g = await api().list_grades();
    modal(`
      <h3>New class</h3>
      <label class="field">Class name <input id="m-name" placeholder="e.g. Grade 6 Blue" autofocus></label>
      <label class="field">Grade / Form
        <select id="m-grade"><option value="">— none —</option>
          ${g.ok ? g.grades.map(gr => `<option value="${esc(gr)}">${esc(gr)}</option>`).join("") : ""}
        </select></label>
      <div class="actions">
        <button class="btn secondary" id="m-cancel">Cancel</button>
        <button class="btn" id="m-ok">Create class</button>
      </div>`,
      bd => {
        bd.querySelector("#m-cancel").onclick = () => bd.remove();
        bd.querySelector("#m-ok").onclick = async () => {
          const res = await api().create_class(
            bd.querySelector("#m-name").value,
            bd.querySelector("#m-grade").value || null);
          if (!res.ok) return toast(res.error);
          bd.remove();
          go("lessons", res.id);
        };
      });
  };
};

/* =============================== lessons =============================== */

routes.lessons = async (classId) => {
  const r = await api().list_lessons(classId);
  if (!r.ok) { toast(r.error); return go("classes"); }
  const rt = await api().list_tests(classId);
  const tests = rt.ok ? rt.tests : [];
  const isOwner = USER.role === "teacher" && r.class.teacher_id === USER.id;

  const rows = r.lessons.length ? `
    <div class="card" style="padding:0">
    <table class="data">
      <tr><th>Lesson</th><th>Subject</th><th>Pages</th><th>Updated</th><th></th></tr>
      ${r.lessons.map(l => `
        <tr>
          <td style="font-weight:600">${esc(l.title)}</td>
          <td>${esc(l.subject_name || "—")}</td>
          <td>${l.page_count}</td>
          <td class="meta">${esc(l.updated_at)}</td>
          <td style="text-align:right;white-space:nowrap">
            <button class="btn sm secondary act-open" data-id="${l.id}"><i class="bi bi-${isOwner ? "pencil" : "eye"}"></i> ${isOwner ? "Edit" : "View"}</button>
            <button class="btn sm act-present" data-id="${l.id}"><i class="bi bi-easel2"></i> Present</button>
            ${isOwner ? `<button class="btn sm danger act-del" data-id="${l.id}"><i class="bi bi-trash"></i></button>` : ""}
          </td>
        </tr>`).join("")}
    </table></div>`
    : `<div class="card empty-state"><i class="bi bi-journal-text"></i>
        No lessons in this class yet.${isOwner ? " Create the first one." : ""}</div>`;

  const testRows = tests.length ? `
    <div class="card" style="padding:0">
    <table class="data">
      <tr><th>Test</th><th>Subject</th><th>Questions</th><th>Marks</th><th>Updated</th><th></th></tr>
      ${tests.map(t => `
        <tr>
          <td style="font-weight:600">${esc(t.title)}</td>
          <td>${esc(t.subject_name || "—")}</td>
          <td>${t.question_count}</td>
          <td>${t.total_marks}</td>
          <td class="meta">${esc(t.updated_at)}</td>
          <td style="text-align:right;white-space:nowrap">
            <button class="btn sm secondary tact-open" data-id="${t.id}"><i class="bi bi-${isOwner ? "pencil" : "eye"}"></i> ${isOwner ? "Edit" : "View"}</button>
            <button class="btn sm tact-present" data-id="${t.id}" title="Questions only"><i class="bi bi-easel2"></i> Present</button>
            <button class="btn sm secondary tact-review" data-id="${t.id}" title="With answers revealed"><i class="bi bi-patch-check"></i> Review</button>
            ${isOwner ? `<button class="btn sm danger tact-del" data-id="${t.id}"><i class="bi bi-trash"></i></button>` : ""}
          </td>
        </tr>`).join("")}
    </table></div>`
    : `<div class="card empty-state"><i class="bi bi-clipboard-check"></i>
        No tests in this class yet.${isOwner ? " Create the first one." : ""}</div>`;

  frame("classes", `
    <div class="page-head">
      <div>
        <h2>${esc(r.class.name)}</h2>
        <div class="sub">${esc(r.class.teacher_name)} · ${r.lessons.length} lesson${r.lessons.length === 1 ? "" : "s"}
          &nbsp; <a href="#" id="back">‹ All classes</a></div>
      </div>
      ${isOwner ? `<div style="display:flex;gap:10px">
        <button class="btn secondary" id="ai-generate-class"><i class="bi bi-stars"></i> Generate with AI</button>
        <button class="btn secondary" id="new-test"><i class="bi bi-clipboard-plus"></i> New test</button>
        <button class="btn" id="new-lesson"><i class="bi bi-plus-lg"></i> New lesson</button>
      </div>` : ""}
    </div>
    <h3 style="margin:4px 0 10px;font-size:15px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em">Lessons</h3>
    ${rows}
    <h3 style="margin:26px 0 10px;font-size:15px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em">Tests</h3>
    ${testRows}`);

  document.getElementById("back").onclick = e => { e.preventDefault(); go("classes"); };

  $app.querySelectorAll(".act-open").forEach(b =>
    b.onclick = () => go(isOwner ? "editor" : "viewer", Number(b.dataset.id), classId));
  $app.querySelectorAll(".act-present").forEach(b =>
    b.onclick = () => startPresenting(Number(b.dataset.id)));
  $app.querySelectorAll(".act-del").forEach(b =>
    b.onclick = async () => {
      if (!confirm("Delete this lesson and all its pages?")) return;
      const res = await api().delete_lesson(Number(b.dataset.id));
      res.ok ? go("lessons", classId) : toast(res.error);
    });

  $app.querySelectorAll(".tact-open").forEach(b =>
    b.onclick = () => go(isOwner ? "testEditor" : "testViewer", Number(b.dataset.id), classId));
  $app.querySelectorAll(".tact-present").forEach(b =>
    b.onclick = () => startTestPresenting(Number(b.dataset.id), false));
  $app.querySelectorAll(".tact-review").forEach(b =>
    b.onclick = () => startTestPresenting(Number(b.dataset.id), true));
  $app.querySelectorAll(".tact-del").forEach(b =>
    b.onclick = async () => {
      if (!confirm("Delete this test and all its questions?")) return;
      const res = await api().delete_test(Number(b.dataset.id));
      res.ok ? go("lessons", classId) : toast(res.error);
    });

  const nt = document.getElementById("new-test");
  if (nt) nt.onclick = async () => {
    const subs = await api().list_subjects();
    modal(`
      <h3>New test</h3>
      <label class="field">Test title <input id="m-title" placeholder="e.g. Fractions — end of topic test" autofocus></label>
      <label class="field">Subject
        <select id="m-subj"><option value="">— none —</option>
          ${subs.subjects.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}
        </select></label>
      <div class="actions">
        <button class="btn secondary" id="m-cancel">Cancel</button>
        <button class="btn" id="m-ok">Create & open builder</button>
      </div>`,
      bd => {
        bd.querySelector("#m-cancel").onclick = () => bd.remove();
        bd.querySelector("#m-ok").onclick = async () => {
          const res = await api().create_test(classId,
            bd.querySelector("#m-title").value,
            Number(bd.querySelector("#m-subj").value) || null);
          if (!res.ok) return toast(res.error);
          bd.remove();
          go("testEditor", res.id, classId);
        };
      });
  };

  const nl = document.getElementById("new-lesson");
  if (nl) nl.onclick = async () => {
    const subs = await api().list_subjects();
    modal(`
      <h3>New lesson</h3>
      <label class="field">Lesson title <input id="m-title" placeholder="e.g. Fractions — adding and subtracting" autofocus></label>
      <label class="field">Subject
        <select id="m-subj"><option value="">— none —</option>
          ${subs.subjects.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}
        </select></label>
      <div class="actions">
        <button class="btn secondary" id="m-cancel">Cancel</button>
        <button class="btn" id="m-ok">Create & open editor</button>
      </div>`,
      bd => {
        bd.querySelector("#m-cancel").onclick = () => bd.remove();
        bd.querySelector("#m-ok").onclick = async () => {
          const res = await api().create_lesson(classId,
            bd.querySelector("#m-title").value,
            Number(bd.querySelector("#m-subj").value) || null);
          if (!res.ok) return toast(res.error);
          bd.remove();
          go("editor", res.id, classId);
        };
      });
  };

  const ag = document.getElementById("ai-generate-class");
  if (ag) ag.onclick = async () => {
    const subs = await api().list_subjects();
    modal(`
      <h3>Generate with AI</h3>
      <label class="field">What do you want to create?
        <select id="m-kind">
          <option value="lesson">Lesson</option>
          <option value="test">Test (quiz)</option>
        </select></label>
      <label class="field">Subject
        <select id="m-subj"><option value="">— none —</option>
          ${subs.subjects.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}
        </select></label>
      <label class="field">Topic <input id="m-topic" placeholder="e.g. Fractions — adding and subtracting" autofocus></label>
      <label class="field" id="m-count-field">Number of questions <input id="m-count" type="number" min="1" max="20" value="10"></label>
      <div class="actions">
        <button class="btn secondary" id="m-cancel">Cancel</button>
        <button class="btn" id="m-ok">Generate</button>
      </div>`,
      bd => {
        const kindSel = bd.querySelector("#m-kind");
        const countField = bd.querySelector("#m-count-field");
        const syncCount = () => countField.style.display = kindSel.value === "test" ? "" : "none";
        kindSel.onchange = syncCount;
        syncCount();
        bd.querySelector("#m-cancel").onclick = () => bd.remove();
        bd.querySelector("#m-ok").onclick = async () => {
          const topic = bd.querySelector("#m-topic").value.trim();
          if (!topic) return toast("Enter a topic.");
          const okBtn = bd.querySelector("#m-ok");
          okBtn.disabled = true;
          okBtn.innerHTML = '<i class="bi bi-arrow-repeat"></i> Generating…';
          const res = await api().ai_generate_and_create(
            classId, kindSel.value,
            Number(bd.querySelector("#m-subj").value) || null,
            topic, Number(bd.querySelector("#m-count").value) || 10);
          okBtn.disabled = false;
          okBtn.innerHTML = "Generate";
          if (!res.ok) return toast(res.error);
          bd.remove();
          go(res.kind === "test" ? "testEditor" : "editor", res.id, classId);
        };
      });
  };
};

/* ============================ lesson editor ============================ */

routes.editor = async (lessonId, classId) => {
  const r = await api().get_lesson(lessonId);
  if (!r.ok) { toast(r.error); return go("classes"); }
  let pages = r.pages;
  let current = 0;
  let dirty = false;

  frame("classes", `
    <div class="page-head">
      <div>
        <h2>${esc(r.lesson.title)}</h2>
        <div class="sub">${esc(r.lesson.class_name)}${r.lesson.subject_name ? " · " + esc(r.lesson.subject_name) : ""}
          &nbsp; <a href="#" id="back">‹ Back to lessons</a></div>
      </div>
      <div style="display:flex;gap:10px;align-items:center">
        <span class="save-state" id="save-state"></span>
        <button class="btn secondary" id="preview-page"><i class="bi bi-eye"></i> Preview</button>
        <button class="btn secondary" id="save-page"><i class="bi bi-check2"></i> Save page</button>
        <button class="btn" id="present"><i class="bi bi-easel2"></i> Present</button>
      </div>
    </div>
    <div class="editor-layout">
      <div class="card pages-panel">
        <div id="page-list"></div>
        <button class="btn ghost sm" id="add-page" style="width:100%;justify-content:center;margin-top:6px">
          <i class="bi bi-plus-lg"></i> Add page</button>
        <button class="btn ghost sm" id="add-sim" style="width:100%;justify-content:center;margin-top:2px">
          <i class="bi bi-joystick"></i> Add simulation</button>
      </div>
      <div class="editor-main">
        <div class="card">
          <div class="editor-title-row">
            <input id="page-title" placeholder="Page title">
            <button class="btn sm danger" id="del-page" title="Delete this page"><i class="bi bi-trash"></i></button>
          </div>
          <div id="text-wrap"><div id="quill-editor"></div></div>
          <div id="sim-wrap" style="display:none">
            <iframe id="sim-frame" style="width:100%;height:480px;border:0;display:block"></iframe>
            <div style="padding:10px 16px;color:var(--muted);font-size:12.5px">
              <i class="bi bi-joystick"></i> Simulation page — fully interactive here and when presented. The title above is editable.</div>
          </div>
        </div>
        <div class="editor-hint">
          <i class="bi bi-info-circle"></i>
          Powers like 5²: select the 2 and press the <b>x²</b> button. Full equations: type <b>$$ ... $$</b> (e.g. <b>$$5^2$$</b> or <b>$$\\frac{1}{2}$$</b>) — use <b>Preview</b> to see them rendered.
          The film icon adds a video from this computer. Drag pages in the left panel to reorder them.
          Images resize by dragging their corners.
        </div>
      </div>
    </div>`);

  document.getElementById("back").onclick = e => {
    e.preventDefault();
    if (dirty && !confirm("Leave without saving this page?")) return;
    go("lessons", classId);
  };

  // Quill + blot-formatter2 (image resize) — the same combination Fundo uses
  quill = new Quill("#quill-editor", {
    theme: "snow",
    modules: {
      toolbar: {
        container: [
          [{ header: [1, 2, 3, false] }],
          ["bold", "italic", "underline"],
          [{ script: "super" }, { script: "sub" }],
          [{ list: "ordered" }, { list: "bullet" }],
          ["image", "video", "link"],
          ["clean"],
        ],
        handlers: {
          video: async function () {
            const res = await api().attach_video(lessonId);
            if (!res.ok) return toast(res.error);
            if (res.cancelled) return;
            const range = quill.getSelection(true);
            quill.insertEmbed(range.index, "localvideo", res.filename, "user");
            quill.setSelection(range.index + 1);
            toast(`Video added (${res.size_mb} MB, copied into the lesson library)`);
          },
        },
      },
      blotFormatter2: {},
    },
  });
  quill.on("text-change", () => {
    if (pages[current] && pages[current].page_type !== "simulation") setDirty(true);
  });
  document.getElementById("page-title").addEventListener("input", () => setDirty(true));

  function setDirty(v) {
    dirty = v;
    const el = document.getElementById("save-state");
    el.textContent = v ? "Unsaved changes" : "Saved";
    el.className = "save-state" + (v ? "" : " saved");
  }

  let dragFrom = null;

  function renderPageList() {
    document.getElementById("page-list").innerHTML = pages.map((p, i) => `
      <div class="page-item ${i === current ? "active" : ""}" data-i="${i}" draggable="true"
           title="Click to open · drag to reorder">
        <span class="n">${i + 1}</span>${p.page_type === "simulation" ? ' <i class="bi bi-joystick"></i>' : ""} <span>${esc(p.title) || "Untitled"}</span>
      </div>`).join("");
    document.querySelectorAll(".page-item").forEach(el => {
      const i = Number(el.dataset.i);
      el.onclick = () => switchPage(i);
      el.addEventListener("dragstart", () => { dragFrom = i; el.style.opacity = ".4"; });
      el.addEventListener("dragend", () => { el.style.opacity = ""; });
      el.addEventListener("dragover", e => { e.preventDefault(); });
      el.addEventListener("drop", async e => {
        e.preventDefault();
        if (dragFrom === null || dragFrom === i) return;
        const activeId = pages[current].id;
        const [moved] = pages.splice(dragFrom, 1);
        pages.splice(i, 0, moved);
        dragFrom = null;
        current = pages.findIndex(p => p.id === activeId);
        renderPageList();
        const res = await api().reorder_pages(lessonId, pages.map(p => p.id));
        if (!res.ok) toast(res.error);
      });
    });
  }

  function loadPage(i) {
    current = i;
    const p = pages[i];
    document.getElementById("page-title").value = p.title;
    const isSim = p.page_type === "simulation";
    document.getElementById("text-wrap").style.display = isSim ? "none" : "";
    document.getElementById("sim-wrap").style.display = isSim ? "" : "none";
    if (isSim) {
      document.getElementById("sim-frame").src = simSrc(p.sim_path);
    } else {
      quill.root.innerHTML = p.content_html || "";
      fixVideoSrcs(quill.root);
    }
    setDirty(false);
    renderPageList();
  }

  async function saveCurrent() {
    const p = pages[current];
    p.title = document.getElementById("page-title").value;
    if (p.page_type !== "simulation") p.content_html = quill.root.innerHTML;
    const res = await api().save_page(p.id, p.title, p.content_html || "");
    if (!res.ok) return toast(res.error);
    setDirty(false);
    renderPageList();
    return true;
  }

  async function switchPage(i) {
    if (i === current) return;
    if (dirty && !(await saveCurrent())) return;
    loadPage(i);
  }

  document.getElementById("save-page").onclick = saveCurrent;

  document.getElementById("add-page").onclick = async () => {
    if (dirty) await saveCurrent();
    const res = await api().add_page(lessonId);
    if (!res.ok) return toast(res.error);
    pages.push({ id: res.id, title: `Page ${res.page_number}`, content_html: "", page_number: res.page_number });
    loadPage(pages.length - 1);
  };

  document.getElementById("del-page").onclick = async () => {
    if (!confirm("Delete this page?")) return;
    const res = await api().delete_page(pages[current].id);
    if (!res.ok) return toast(res.error);
    pages.splice(current, 1);
    loadPage(Math.max(0, current - 1));
  };

  document.getElementById("present").onclick = async () => {
    if (dirty) await saveCurrent();
    startPresenting(lessonId);
  };

  document.getElementById("add-sim").onclick = async () => {
    if (dirty) await saveCurrent();
    const r = await api().list_sims();
    if (!r.ok) return toast(r.error);
    if (!r.sims.length) {
      return modal(`<h3>No simulations installed</h3>
        <p style="color:var(--muted)">Ask your administrator to install simulations first (Admin &rarr; Simulations).</p>
        <div class="actions"><button class="btn secondary" id="m-ok">Close</button></div>`,
        bd => bd.querySelector("#m-ok").onclick = () => bd.remove());
    }
    modal(`<h3>Add simulation page</h3>
      <div style="max-height:340px;overflow-y:auto">${r.sims.map(s => `
        <div class="page-item" data-id="${s.id}" style="padding:12px">
          <i class="bi bi-joystick"></i> <span>${esc(s.title)}</span>
          ${s.kind === "ggb" && !r.ggb_ready ? ' <span class="badge inactive">needs GeoGebra runtime</span>' : ""}
        </div>`).join("")}</div>
      <div class="actions"><button class="btn secondary" id="m-cancel">Cancel</button></div>`,
      bd => {
        bd.querySelector("#m-cancel").onclick = () => bd.remove();
        bd.querySelectorAll(".page-item").forEach(el => el.onclick = async () => {
          const res = await api().add_sim_page(lessonId, Number(el.dataset.id));
          if (!res.ok) return toast(res.error);
          bd.remove();
          pages.push({ id: res.id, title: res.title, content_html: "",
                       page_number: res.page_number, page_type: "simulation", sim_path: res.sim_path });
          loadPage(pages.length - 1);
        });
      });
  };

  document.getElementById("preview-page").onclick = () => {
    const title = document.getElementById("page-title").value;
    const bd = modal(`
      <div class="meta" style="color:var(--muted);font-size:12px;font-weight:700;text-transform:uppercase">
        Preview — how this page will look when presented</div>
      <h3>${esc(title)}</h3>
      <div class="lesson-content ql-editor" id="pv-content"></div>
      <div class="actions"><button class="btn secondary" id="pv-close">Close</button></div>`);
    bd.querySelector(".modal").style.width = "760px";
    bd.querySelector(".modal").style.maxHeight = "85vh";
    bd.querySelector(".modal").style.overflowY = "auto";
    const cp = pages[current];
    if (cp.page_type === "simulation") {
      bd.querySelector("#pv-content").innerHTML =
        '<iframe style="width:100%;height:60vh;border:0" src="' + simSrc(cp.sim_path) + '"></iframe>';
    } else {
      bd.querySelector("#pv-content").innerHTML = quill.root.innerHTML;
      fixVideoSrcs(bd.querySelector("#pv-content"));
      renderMath(bd.querySelector("#pv-content"));
    }
    bd.querySelector("#pv-close").onclick = () => bd.remove();
  };

  loadPage(0);
};

/* =========================== read-only viewer ========================== */

routes.viewer = async (lessonId, classId) => {
  const r = await api().get_lesson(lessonId);
  if (!r.ok) { toast(r.error); return go("classes"); }

  frame("classes", `
    <div class="page-head">
      <div>
        <h2>${esc(r.lesson.title)}</h2>
        <div class="sub">${esc(r.lesson.teacher_name)} · ${esc(r.lesson.class_name)}
          &nbsp; <a href="#" id="back">‹ Back</a></div>
      </div>
      <button class="btn" id="present"><i class="bi bi-easel2"></i> Present</button>
    </div>
    ${r.pages.map((p, i) => `
      <div class="card" style="margin-bottom:16px">
        <div class="meta" style="color:var(--muted);font-size:12px;font-weight:700;text-transform:uppercase">Page ${i + 1}</div>
        <h3>${esc(p.title)}</h3>
        ${p.page_type === "simulation"
          ? `<iframe style="width:100%;height:480px;border:0" src="${simSrc(p.sim_path)}"></iframe>`
          : `<div class="lesson-content ql-editor">${p.content_html}</div>`}
      </div>`).join("")}`);

  fixVideoSrcs($app.querySelector(".main"));
  renderMath($app.querySelector(".main"));
  document.getElementById("back").onclick = e => { e.preventDefault(); go("lessons", classId); };
  document.getElementById("present").onclick = () => startPresenting(lessonId);
};

/* ============================ test builder ============================= */

const KIND_LABELS = {
  multiple_choice: "Multiple choice",
  true_false: "True / False",
  short_answer: "Short answer",
  long_answer: "Long answer",
};
const KIND_ICONS = {
  multiple_choice: "bi-ui-radios",
  true_false: "bi-toggle2-on",
  short_answer: "bi-input-cursor-text",
  long_answer: "bi-card-text",
};

routes.testEditor = async (testId, classId) => {
  const r = await api().get_test(testId);
  if (!r.ok) { toast(r.error); return go("classes"); }
  let questions = r.questions;
  let current = 0;
  let dragFrom = null;

  const totalMarks = () => questions.reduce((s, q) => s + (Number(q.marks) || 0), 0);

  frame("classes", `
    <div class="page-head">
      <div>
        <h2>${esc(r.test.title)}</h2>
        <div class="sub">${esc(r.test.class_name)}${r.test.subject_name ? " \u00b7 " + esc(r.test.subject_name) : ""}
          \u00b7 <span id="marks-total">${totalMarks()}</span> marks
          &nbsp; <a href="#" id="back">\u2039 Back to class</a></div>
      </div>
      <div style="display:flex;gap:10px;align-items:center">
        <button class="btn secondary" id="t-present"><i class="bi bi-easel2"></i> Present</button>
        <button class="btn" id="t-review"><i class="bi bi-patch-check"></i> Review with answers</button>
      </div>
    </div>
    <div class="card" style="margin-bottom:18px">
      <div style="display:flex;gap:14px;align-items:flex-end;flex-wrap:wrap">
        <label class="field" style="flex:1;min-width:220px;margin-bottom:0">Test title
          <input id="t-title" value="${esc(r.test.title)}"></label>
        <label class="field" style="flex:2;min-width:280px;margin-bottom:0">Instructions (shown at the start)
          <input id="t-instr" value="${esc(r.test.instructions)}" placeholder="e.g. Answer all questions in your exercise book. Show your working."></label>
        <label class="field" style="width:230px;margin-bottom:0">Presentation layout
          <select id="t-layout">
            <option value="pages" ${r.test.layout !== "single" ? "selected" : ""}>One question per page</option>
            <option value="single" ${r.test.layout === "single" ? "selected" : ""}>All questions on one page</option>
          </select></label>
        <button class="btn secondary" id="t-save-meta"><i class="bi bi-check2"></i> Save details</button>
      </div>
    </div>
    <div class="editor-layout">
      <div class="card pages-panel">
        <div id="q-list"></div>
        <button class="btn ghost sm" id="add-q" style="width:100%;justify-content:center;margin-top:6px">
          <i class="bi bi-plus-lg"></i> Add question</button>
      </div>
      <div class="editor-main">
        <div class="card" style="padding:22px" id="q-form"></div>
        <div class="editor-hint"><i class="bi bi-info-circle"></i>
          Questions support equations: type <b>$$ ... $$</b> (e.g. <b>$$\\frac{3}{4} \\times 8$$</b>) \u2014 rendered when presenting.
          Drag questions in the left panel to reorder. Switching questions saves automatically.</div>
      </div>
    </div>`);

  document.getElementById("back").onclick = async e => {
    e.preventDefault();
    await saveCurrentQ();
    go("lessons", classId);
  };
  document.getElementById("t-save-meta").onclick = async () => {
    const res = await api().update_test(testId,
      document.getElementById("t-title").value,
      document.getElementById("t-instr").value,
      document.getElementById("t-layout").value);
    res.ok ? toast("Test details saved") : toast(res.error);
  };
  document.getElementById("t-present").onclick = async () => {
    await saveCurrentQ();
    startTestPresenting(testId, false);
  };
  document.getElementById("t-review").onclick = async () => {
    await saveCurrentQ();
    startTestPresenting(testId, true);
  };

  function renderQList() {
    document.getElementById("q-list").innerHTML = questions.map((q, i) => `
      <div class="page-item ${i === current ? "active" : ""}" data-i="${i}" draggable="true"
           title="Click to open \u00b7 drag to reorder">
        <span class="n">${i + 1}</span> <i class="bi ${KIND_ICONS[q.kind]}"></i>
        <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(q.prompt) || "Untitled question"}</span>
      </div>`).join("");
    document.querySelectorAll("#q-list .page-item").forEach(el => {
      const i = Number(el.dataset.i);
      el.onclick = () => switchQ(i);
      el.addEventListener("dragstart", () => { dragFrom = i; el.style.opacity = ".4"; });
      el.addEventListener("dragend", () => { el.style.opacity = ""; });
      el.addEventListener("dragover", e => e.preventDefault());
      el.addEventListener("drop", async e => {
        e.preventDefault();
        if (dragFrom === null || dragFrom === i) return;
        const activeId = questions[current].id;
        const [moved] = questions.splice(dragFrom, 1);
        questions.splice(i, 0, moved);
        dragFrom = null;
        current = questions.findIndex(q => q.id === activeId);
        renderQList();
        const res = await api().reorder_questions(testId, questions.map(q => q.id));
        if (!res.ok) toast(res.error);
      });
    });
  }

  function optionRow(text, idx, checked) {
    return `
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px" class="opt-row">
        <input type="radio" name="q-correct" title="Mark as the correct answer" ${checked ? "checked" : ""}>
        <b style="width:20px">${String.fromCharCode(65 + idx)}.</b>
        <input class="opt-text" value="${esc(text)}" placeholder="Option text"
               style="flex:1;padding:8px 10px;border:1px solid var(--border);border-radius:8px">
        <button class="btn sm secondary opt-del" title="Remove option"><i class="bi bi-x-lg"></i></button>
      </div>`;
  }

  function renderForm() {
    const q = questions[current];
    const f = document.getElementById("q-form");
    let body = "";
    if (q.kind === "multiple_choice") {
      const correct = parseInt(q.answer, 10);
      body = `
        <label class="field" style="margin-bottom:6px">Options \u2014 tick the correct one</label>
        <div id="opts">${(q.options.length ? q.options : ["", ""]).map((o, i) =>
          optionRow(o, i, i === correct)).join("")}</div>
        <button class="btn ghost sm" id="opt-add"><i class="bi bi-plus-lg"></i> Add option</button>`;
    } else if (q.kind === "true_false") {
      body = `
        <label class="field">Correct answer
          <select id="q-tf">
            <option value="true" ${q.answer === "true" ? "selected" : ""}>True</option>
            <option value="false" ${q.answer !== "true" ? "selected" : ""}>False</option>
          </select></label>`;
    } else {
      body = `
        <label class="field">Model answer <span style="font-weight:400">(shown in Review mode)</span>
          <input id="q-model" value="${esc(q.answer)}" placeholder="${q.kind === "long_answer" ? "Key points a full answer should include" : "The expected answer"}"></label>`;
    }
    f.innerHTML = `
      <div style="display:flex;gap:14px;flex-wrap:wrap">
        <label class="field" style="flex:1;min-width:200px">Question type
          <select id="q-kind">${Object.entries(KIND_LABELS).map(([k, lbl]) =>
            `<option value="${k}" ${q.kind === k ? "selected" : ""}>${lbl}</option>`).join("")}</select></label>
        <label class="field" style="width:110px">Marks
          <input id="q-marks" type="number" min="0" value="${q.marks}"></label>
      </div>
      <label class="field">Question
        <textarea id="q-prompt" rows="3"
          style="display:block;width:100%;margin-top:6px;padding:10px 12px;font-size:15px;font-family:inherit;border:1px solid var(--border);border-radius:10px">${esc(q.prompt)}</textarea></label>
      ${body}
      <label class="field" style="margin-top:14px">Explanation <span style="font-weight:400">(optional \u2014 shown in Review mode as \u201cWhy\u201d)</span>
        <input id="q-expl" value="${esc(q.explanation)}"></label>
      <div style="display:flex;justify-content:space-between;margin-top:10px">
        <button class="btn danger sm" id="q-del"><i class="bi bi-trash"></i> Delete question</button>
        <button class="btn" id="q-save"><i class="bi bi-check2"></i> Save question</button>
      </div>`;

    f.querySelector("#q-kind").onchange = async e => {
      q.kind = e.target.value;
      q.answer = q.kind === "true_false" ? "true" : "";
      renderForm();
    };
    const addBtn = f.querySelector("#opt-add");
    if (addBtn) addBtn.onclick = () => {
      collect();
      q.options.push("");
      renderForm();
    };
    f.querySelectorAll(".opt-del").forEach((b, i) => b.onclick = () => {
      collect();
      if (q.options.length <= 2) return toast("Multiple choice needs at least two options.");
      q.options.splice(i, 1);
      if (parseInt(q.answer, 10) === i) q.answer = "";
      renderForm();
    });
    f.querySelector("#q-save").onclick = async () => {
      const ok = await saveCurrentQ();
      if (ok) toast("Question saved");
    };
    f.querySelector("#q-del").onclick = async () => {
      if (!confirm("Delete this question?")) return;
      const res = await api().delete_question(q.id);
      if (!res.ok) return toast(res.error);
      questions.splice(current, 1);
      current = Math.max(0, current - 1);
      renderQList(); renderForm(); updateMarks();
    };
    f.querySelector("#q-marks").addEventListener("input", updateMarksLive);
  }

  function collect() {
    const q = questions[current];
    const f = document.getElementById("q-form");
    q.prompt = f.querySelector("#q-prompt").value;
    q.marks = Number(f.querySelector("#q-marks").value) || 0;
    q.explanation = f.querySelector("#q-expl").value;
    if (q.kind === "multiple_choice") {
      q.options = [...f.querySelectorAll(".opt-text")].map(el => el.value);
      const radios = [...f.querySelectorAll('input[name="q-correct"]')];
      const idx = radios.findIndex(rd => rd.checked);
      q.answer = idx >= 0 ? String(idx) : "";
    } else if (q.kind === "true_false") {
      q.answer = f.querySelector("#q-tf").value;
    } else {
      q.answer = f.querySelector("#q-model").value;
    }
  }

  async function saveCurrentQ() {
    collect();
    const q = questions[current];
    const res = await api().save_question(q.id, q.kind, q.prompt, q.options || [],
                                          q.answer, q.explanation, q.marks);
    if (!res.ok) { toast(res.error); return false; }
    renderQList(); updateMarks();
    return true;
  }

  async function switchQ(i) {
    if (i === current) return;
    if (!(await saveCurrentQ())) return;
    current = i;
    renderQList(); renderForm();
  }

  function updateMarks() {
    document.getElementById("marks-total").textContent = totalMarks();
  }
  function updateMarksLive() {
    collect(); updateMarks();
  }

  document.getElementById("add-q").onclick = async () => {
    await saveCurrentQ();
    modal(`<h3>Add question</h3>
      ${Object.entries(KIND_LABELS).map(([k, lbl]) => `
        <div class="page-item" data-k="${k}" style="padding:12px"><i class="bi ${KIND_ICONS[k]}"></i> <span>${lbl}</span></div>`).join("")}
      <div class="actions"><button class="btn secondary" id="m-cancel">Cancel</button></div>`,
      bd => {
        bd.querySelector("#m-cancel").onclick = () => bd.remove();
        bd.querySelectorAll(".page-item").forEach(el => el.onclick = async () => {
          const res = await api().add_question(testId, el.dataset.k);
          if (!res.ok) return toast(res.error);
          bd.remove();
          questions.push({ id: res.id, kind: res.kind, prompt: "", options: [],
                           answer: res.kind === "true_false" ? "true" : "",
                           explanation: "", marks: 1 });
          current = questions.length - 1;
          renderQList(); renderForm(); updateMarks();
        });
      });
  };

  renderQList();
  renderForm();
};

/* ===================== test viewer (read-only, with key) =============== */

routes.testViewer = async (testId, classId) => {
  const r = await api().test_pages(testId, true);
  if (!r.ok) { toast(r.error); return go("classes"); }
  frame("classes", `
    <div class="page-head">
      <div>
        <h2>${esc(r.test.title)}</h2>
        <div class="sub">${esc(r.test.teacher_name)} \u00b7 ${esc(r.test.class_name)} \u00b7 answer key shown
          &nbsp; <a href="#" id="back">\u2039 Back</a></div>
      </div>
      <div style="display:flex;gap:10px">
        <button class="btn secondary" id="t-present"><i class="bi bi-easel2"></i> Present</button>
        <button class="btn" id="t-review"><i class="bi bi-patch-check"></i> Review with answers</button>
      </div>
    </div>
    ${r.pages.map(p => `
      <div class="card" style="margin-bottom:16px">
        <h3>${esc(p.title)}</h3>
        <div class="lesson-content">${p.content_html}</div>
      </div>`).join("")}`);
  renderMath($app.querySelector(".main"));
  document.getElementById("back").onclick = e => { e.preventDefault(); go("lessons", classId); };
  document.getElementById("t-present").onclick = () => startTestPresenting(testId, false);
  document.getElementById("t-review").onclick = () => startTestPresenting(testId, true);
};

/* ============================= presenting ============================== */

async function startTestPresenting(testId, reveal) {
  const r = await api().start_test_presentation(testId, reveal);
  if (!r.ok) return toast(r.error);
  if (!r.external_display) toast("No projector detected \u2014 presenting on this screen");
  presenter = { index: 0, count: r.page_count };
  renderPresentBar();
}


async function startPresenting(lessonId) {
  const r = await api().start_presentation(lessonId);
  if (!r.ok) return toast(r.error);
  if (!r.external_display) toast("No projector detected — presenting on this screen");
  presenter = { lessonId, index: 0, count: r.page_count };
  renderPresentBar();
}

function renderPresentBar() {
  let bar = document.getElementById("present-bar");
  if (!presenter) { if (bar) bar.remove(); return; }
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "present-bar";
    bar.className = "present-bar";
    document.body.appendChild(bar);
  }
  bar.innerHTML = `
    <button id="pb-prev" title="Previous page"><i class="bi bi-chevron-left"></i></button>
    <span class="pg">${presenter.index + 1} / ${presenter.count}</span>
    <button id="pb-next" title="Next page"><i class="bi bi-chevron-right"></i></button>
    <span style="width:1px;height:22px;background:rgba(255,255,255,.25)"></span>
    <button id="pb-up" title="Scroll up"><i class="bi bi-chevron-up"></i></button>
    <button id="pb-down" title="Scroll down"><i class="bi bi-chevron-down"></i></button>
    <button class="stop" id="pb-stop">End presentation</button>`;
  bar.querySelector("#pb-prev").onclick = () => presentGoto(presenter.index - 1);
  bar.querySelector("#pb-next").onclick = () => presentGoto(presenter.index + 1);
  bar.querySelector("#pb-up").onclick = () => api().present_scroll(-1);
  bar.querySelector("#pb-down").onclick = () => api().present_scroll(1);
  bar.querySelector("#pb-stop").onclick = async () => {
    await api().stop_presentation();
    presenter = null;
    renderPresentBar();
  };
}

async function presentGoto(i) {
  if (!presenter || i < 0 || i >= presenter.count) return;
  const r = await api().present_goto(i);
  if (!r.ok) return toast(r.error);
  presenter.index = i;
  renderPresentBar();
}

document.addEventListener("keydown", e => {
  if (!presenter) return;
  if (e.key === "ArrowRight" || e.key === "PageDown") presentGoto(presenter.index + 1);
  if (e.key === "ArrowLeft" || e.key === "PageUp") presentGoto(presenter.index - 1);
});

/* Called from Python when the presentation window navigates itself
   (arrow keys / touch arrows at the board) — keeps the bar in step. */
window.presenterSync = i => {
  if (!presenter) return;
  presenter.index = i;
  renderPresentBar();
};

/* Called from Python when the presentation window closes for any reason
   (Esc at the board, Alt+F4, stop button). */
window.presenterEnded = () => {
  presenter = null;
  renderPresentBar();
};

/* ============================ users (admin) ============================ */

routes.users = async () => {
  const r = await api().list_users();
  if (!r.ok) { toast(r.error); return go("dashboard"); }
  const isAdmin = USER.role === "admin";

  frame("users", `
    <div class="page-head">
      <div><h2>Accounts</h2><div class="sub">Everyone who can sign in on this device</div></div>
      ${isAdmin ? `<button class="btn" id="new-user"><i class="bi bi-person-plus"></i> New account</button>` : ""}
    </div>
    <div class="card" style="padding:0">
    <table class="data">
      <tr><th>Name</th><th>Username</th><th>Role</th><th>Status</th>${isAdmin ? "<th></th>" : ""}</tr>
      ${r.users.map(u => `
        <tr>
          <td style="font-weight:600">${esc(u.full_name)}</td>
          <td>${esc(u.username)}</td>
          <td><span class="badge ${u.role}">${u.role}</span></td>
          <td>${u.active ? "Active" : `<span class="badge inactive">Deactivated</span>`}</td>
          ${isAdmin ? `<td style="text-align:right;white-space:nowrap">
            <button class="btn sm secondary act-pass" data-id="${u.id}"><i class="bi bi-key"></i> Reset password</button>
            ${u.id !== USER.id ? `<button class="btn sm ${u.active ? "danger" : ""} act-tog" data-id="${u.id}" data-a="${u.active}">
              ${u.active ? "Deactivate" : "Reactivate"}</button>` : ""}
          </td>` : ""}
        </tr>`).join("")}
    </table></div>`);

  if (!isAdmin) return;

  document.getElementById("new-user").onclick = () => modal(`
    <h3>New account</h3>
    <label class="field">Full name <input id="m-name" autofocus></label>
    <label class="field">Username <input id="m-user"></label>
    <label class="field">Password <input id="m-pass" type="password"></label>
    <label class="field">Role
      <select id="m-role">
        <option value="teacher">Teacher — creates and presents their own lessons</option>
        <option value="supervisor">Supervisor — read-only view of all classes</option>
        <option value="admin">Admin — manages accounts and settings</option>
      </select></label>
    <div class="actions">
      <button class="btn secondary" id="m-cancel">Cancel</button>
      <button class="btn" id="m-ok">Create account</button>
    </div>`,
    bd => {
      bd.querySelector("#m-cancel").onclick = () => bd.remove();
      bd.querySelector("#m-ok").onclick = async () => {
        const g = id => bd.querySelector(id).value;
        const res = await api().create_user(g("#m-name"), g("#m-user"), g("#m-pass"), g("#m-role"));
        if (!res.ok) return toast(res.error);
        bd.remove(); go("users");
      };
    });

  $app.querySelectorAll(".act-tog").forEach(b =>
    b.onclick = async () => {
      const res = await api().set_user_active(Number(b.dataset.id), b.dataset.a !== "1");
      res.ok ? go("users") : toast(res.error);
    });

  $app.querySelectorAll(".act-pass").forEach(b =>
    b.onclick = () => modal(`
      <h3>Reset password</h3>
      <label class="field">New password <input id="m-pass" type="password" autofocus></label>
      <div class="actions">
        <button class="btn secondary" id="m-cancel">Cancel</button>
        <button class="btn" id="m-ok">Reset</button>
      </div>`,
      bd => {
        bd.querySelector("#m-cancel").onclick = () => bd.remove();
        bd.querySelector("#m-ok").onclick = async () => {
          const res = await api().reset_user_password(Number(b.dataset.id), bd.querySelector("#m-pass").value);
          if (!res.ok) return toast(res.error);
          bd.remove(); toast("Password reset");
        };
      }));
};

/* ======================== simulations (admin) ========================= */

routes.sims = async () => {
  const r = await api().list_sims();
  if (!r.ok) { toast(r.error); return go("dashboard"); }

  const rows = r.sims.length ? `
    <div class="card" style="padding:0">
    <table class="data">
      <tr><th>Simulation</th><th>Type</th><th>Installed</th><th></th></tr>
      ${r.sims.map(s => `
        <tr>
          <td style="font-weight:600">${esc(s.title)}</td>
          <td><span class="badge ${s.kind === "ggb" ? "supervisor" : "teacher"}">${s.kind === "ggb" ? "GeoGebra" : "PhET / HTML"}</span></td>
          <td class="meta">${esc(s.created_at)}</td>
          <td style="text-align:right">
            <button class="btn sm danger act-del" data-id="${s.id}"><i class="bi bi-trash"></i></button></td>
        </tr>`).join("")}
    </table></div>`
    : `<div class="card empty-state"><i class="bi bi-joystick"></i>
        No simulations installed yet. Install from a file, or open the PhET catalog while the internet dongle is connected.</div>`;

  frame("sims", `
    <div class="page-head">
      <div><h2>Simulations</h2>
        <div class="sub">Shared library — every teacher on this device can add these to lessons</div></div>
      <div style="display:flex;gap:10px">
        <button class="btn secondary" id="inst-file"><i class="bi bi-folder2-open"></i> Install from file</button>
        <button class="btn" id="phet-cat"><i class="bi bi-cloud-download"></i> PhET catalog</button>
      </div>
    </div>
    <div class="card" style="margin-bottom:18px;display:flex;justify-content:space-between;align-items:center;gap:16px">
      <div>
        <h3 style="font-size:16px;margin-bottom:4px">GeoGebra runtime
          ${r.ggb_ready ? '<span class="badge teacher">Installed</span>' : '<span class="badge inactive">Not installed</span>'}</h3>
        <div class="meta" style="color:var(--muted);font-size:13px">
          ${r.ggb_ready
            ? "GeoGebra .ggb files play inside lessons, fully offline."
            : "Download the free “GeoGebra Math Apps Bundle” zip from geogebra.org/download on a connected machine, then install it here. Until then, .ggb files can be stored but not played."}</div>
      </div>
      <button class="btn secondary" id="inst-ggb" style="flex-shrink:0">
        <i class="bi bi-box-seam"></i> ${r.ggb_ready ? "Reinstall" : "Install"} runtime</button>
    </div>
    ${rows}
    <p class="editor-hint" style="margin-top:14px"><i class="bi bi-info-circle"></i>
      Simulations by PhET Interactive Simulations, University of Colorado Boulder (CC-BY-NC).
      Catalog access uses PhET's metadata service under your organisation's licence agreement with PhET,
      and requires the internet dongle. Downloaded sims run fully offline afterwards.</p>`);

  document.getElementById("inst-ggb").onclick = async () => {
    const res = await api().install_geogebra_runtime();
    if (!res.ok) return toast(res.error);
    if (res.cancelled) return;
    toast("GeoGebra runtime installed");
    go("sims");
  };

  document.getElementById("inst-file").onclick = async () => {
    const res = await api().install_sim_file();
    if (!res.ok) return toast(res.error);
    if (res.cancelled) return;
    toast(`Installed \u201c${res.title}\u201d`);
    go("sims");
  };

  $app.querySelectorAll(".act-del").forEach(b =>
    b.onclick = async () => {
      if (!confirm("Remove this simulation from the library?")) return;
      const res = await api().delete_sim(Number(b.dataset.id));
      res.ok ? go("sims") : toast(res.error);
    });

  document.getElementById("phet-cat").onclick = async () => {
    let installed = false;
    const bd = modal(`
      <h3>PhET catalog</h3>
      <div id="cat-body"><p style="color:var(--muted)"><i class="bi bi-arrow-repeat"></i>
        Fetching the catalog\u2026 this needs the internet dongle connected.</p></div>
      <div class="actions"><button class="btn secondary" id="m-close">Close</button></div>`);
    bd.querySelector(".modal").style.width = "640px";
    bd.querySelector("#m-close").onclick = () => { bd.remove(); if (installed) go("sims"); };

    const r = await api().phet_catalog();
    const body = bd.querySelector("#cat-body");
    if (!r.ok) { body.innerHTML = `<div class="error-msg">${esc(r.error)}</div>`; return; }

    body.innerHTML = `
      <p style="color:var(--muted);font-size:13px">${r.count} simulations available.
        Each downloads as a single file and runs offline forever after.</p>
      <label class="field">Search <input id="cat-q" placeholder="e.g. fractions, electricity, gravity\u2026"></label>
      <div id="cat-list" style="max-height:320px;overflow-y:auto"></div>`;
    const list = body.querySelector("#cat-list");

    const render = q => {
      const items = r.sims.filter(s => s.title.toLowerCase().includes(q)).slice(0, 60);
      list.innerHTML = items.map(s => `
        <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 4px;border-bottom:1px solid var(--border)">
          <span>${esc(s.title)}</span>
          <button class="btn sm secondary cat-dl" data-i="${r.sims.indexOf(s)}" title="Download to library">
            <i class="bi bi-download"></i></button>
        </div>`).join("") || `<p style="color:var(--muted)">No matches.</p>`;
      list.querySelectorAll(".cat-dl").forEach(b => b.onclick = async () => {
        const s = r.sims[Number(b.dataset.i)];
        b.disabled = true; b.innerHTML = '<i class="bi bi-arrow-repeat"></i>';
        const res = await api().phet_download(s.title, s.run_url);
        if (!res.ok) {
          toast(res.error);
          b.disabled = false; b.innerHTML = '<i class="bi bi-download"></i>';
          return;
        }
        installed = true;
        toast(`Installed \u201c${s.title}\u201d`);
        b.innerHTML = '<i class="bi bi-check-lg"></i>';
      });
    };
    body.querySelector("#cat-q").addEventListener("input", e => render(e.target.value.toLowerCase().trim()));
    render("");
  };
};

/* =========================== settings (admin) ========================== */

routes.settings = async () => {
  const s = await api().get_settings();
  const subs = await api().list_subjects();
  frame("settings", `
    <div class="page-head"><div><h2>Settings</h2><div class="sub">School details and subjects</div></div></div>
    <div class="card" style="max-width:520px;margin-bottom:20px">
      <h3 style="font-size:16px">School</h3>
      <label class="field">School name <input id="s-name" value="${esc(s.settings.school_name)}"></label>
      <label class="field">Location <input id="s-loc" value="${esc(s.settings.location || "")}"></label>
      <button class="btn" id="s-save"><i class="bi bi-check2"></i> Save changes</button>
    </div>
    <div class="card" style="max-width:520px">
      <h3 style="font-size:16px">Subjects</h3>
      <p class="sub" style="color:var(--muted)">${subs.subjects.map(x => esc(x.name)).join(" · ")}</p>
      <div style="display:flex;gap:10px">
        <input id="sub-name" placeholder="Add a subject…" style="flex:1;padding:10px 12px;border:1px solid var(--border);border-radius:10px">
        <button class="btn secondary" id="sub-add"><i class="bi bi-plus-lg"></i> Add</button>
      </div>
    </div>`);

  document.getElementById("s-save").onclick = async () => {
    const r = await api().update_settings(
      document.getElementById("s-name").value,
      document.getElementById("s-loc").value);
    r.ok ? toast("Settings saved") : toast(r.error);
  };
  document.getElementById("sub-add").onclick = async () => {
    const r = await api().create_subject(document.getElementById("sub-name").value);
    r.ok ? go("settings") : toast(r.error);
  };
};

/* ======================== AI settings (admin) ========================= */

routes.aiSettings = async () => {
  const s = await api().get_ai_settings();
  if (!s.ok) { toast(s.error); return go("dashboard"); }
  const status = await api().ai_get_status();
  const settings = s.settings;

  const localBadge = status.ok && status.local_model_available
    ? `<span class="badge">found: ${esc(status.local_model_info?.model || "a .gguf model")}</span>`
    : `<span class="badge inactive">no local model found</span>`;
  const cloudBadge = status.ok && status.cloud_configured
    ? `<span class="badge">key configured</span>`
    : `<span class="badge inactive">no key configured</span>`;

  frame("aiSettings", `
    <div class="page-head"><div><h2>AI Settings</h2>
      <div class="sub">Configure how teachers generate quizzes, flashcards and lesson drafts</div></div></div>
    <div class="card" style="max-width:560px;margin-bottom:20px">
      <h3 style="font-size:16px">Provider</h3>
      <label class="field">Mode
        <select id="ai-provider">
          <option value="cloud" ${settings.provider === "cloud" ? "selected" : ""}>Cloud (Anthropic) only</option>
          <option value="local" ${settings.provider === "local" ? "selected" : ""}>Local model only (offline)</option>
          <option value="auto" ${settings.provider === "auto" ? "selected" : ""}>Cloud first, fall back to local</option>
        </select>
      </label>
      <p class="sub" style="color:var(--muted)">Local generation runs fully offline on this laptop's CPU. Cloud generation needs
        an internet connection (the 4G dongle) and an Anthropic API key.</p>
    </div>
    <div class="card" style="max-width:560px;margin-bottom:20px">
      <h3 style="font-size:16px">Cloud (Anthropic) ${cloudBadge}</h3>
      <label class="field">API key <input id="ai-key" type="password" placeholder="${settings.api_key_set ? "•••••••• (leave blank to keep current key)" : "sk-ant-…"}"></label>
    </div>
    <div class="card" style="max-width:560px">
      <h3 style="font-size:16px">Local model ${localBadge}</h3>
      <label class="field">GGUF filename in the models folder <input id="ai-model" value="${esc(settings.model_filename || "")}" placeholder="leave blank to auto-pick the largest .gguf found"></label>
      <p class="sub" style="color:var(--muted)">Copy a .gguf model file into <code>%LOCALAPPDATA%\\NhavaLearn\\models</code>
        on this laptop, then enter its filename here (or leave blank to use the largest one found automatically).</p>
      <button class="btn" id="ai-save"><i class="bi bi-check2"></i> Save changes</button>
    </div>`);

  document.getElementById("ai-save").onclick = async () => {
    const provider = document.getElementById("ai-provider").value;
    const apiKey = document.getElementById("ai-key").value || null;
    const modelFilename = document.getElementById("ai-model").value || null;
    const r = await api().update_ai_settings(provider, apiKey, modelFilename);
    r.ok ? go("aiSettings") : toast(r.error);
    if (r.ok) toast("AI settings saved");
  };
};

/* ======================== Library documents (admin) =================== */

routes.documents = async () => {
  const d = await api().list_documents();
  if (!d.ok) { toast(d.error); return go("dashboard"); }
  const subs = await api().list_subjects();
  const grades = await api().list_grades();

  const rows = d.documents.length ? `
    <div class="card" style="padding:0">
    <table class="data">
      <tr><th>Title</th><th>Grade</th><th>Subject</th><th>Uploaded</th><th></th></tr>
      ${d.documents.map(doc => `
        <tr>
          <td style="font-weight:600">${esc(doc.title)}</td>
          <td>${esc(doc.grade)}</td>
          <td>${esc(doc.subject_name || "—")}</td>
          <td class="meta">${esc(doc.created_at)}</td>
          <td style="text-align:right"><button class="btn sm danger doc-del" data-id="${doc.id}"><i class="bi bi-trash"></i></button></td>
        </tr>`).join("")}
    </table></div>`
    : `<div class="card empty-state"><i class="bi bi-file-earmark-text"></i>
        No library documents yet. Upload a PDF or Word document so teachers can generate AI content grounded in it.</div>`;

  frame("documents", `
    <div class="page-head">
      <div><h2>Library Documents</h2>
        <div class="sub">Reference material teachers draw on when generating lessons and tests with AI</div></div>
      <button class="btn" id="doc-upload"><i class="bi bi-upload"></i> Upload document</button>
    </div>
    ${rows}`);

  $app.querySelectorAll(".doc-del").forEach(b =>
    b.onclick = async () => {
      if (!confirm("Delete this document?")) return;
      const res = await api().delete_document(Number(b.dataset.id));
      res.ok ? go("documents") : toast(res.error);
    });

  document.getElementById("doc-upload").onclick = () => modal(`
      <h3>Upload document</h3>
      <label class="field">Title <input id="m-title" placeholder="e.g. Grade 6 Mathematics Syllabus" autofocus></label>
      <label class="field">Grade / Form
        <select id="m-grade">
          ${grades.ok ? grades.grades.map(gr => `<option value="${esc(gr)}">${esc(gr)}</option>`).join("") : ""}
        </select></label>
      <label class="field">Subject
        <select id="m-subj"><option value="">— none —</option>
          ${subs.subjects.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}
        </select></label>
      <p class="sub" style="color:var(--muted)">PDF or Word (.docx) only. You'll be asked to pick the file next.</p>
      <div class="actions">
        <button class="btn secondary" id="m-cancel">Cancel</button>
        <button class="btn" id="m-ok">Choose file & upload</button>
      </div>`,
    bd => {
      bd.querySelector("#m-cancel").onclick = () => bd.remove();
      bd.querySelector("#m-ok").onclick = async () => {
        const title = bd.querySelector("#m-title").value.trim();
        if (!title) return toast("Enter a title.");
        const res = await api().upload_document(
          title,
          Number(bd.querySelector("#m-subj").value) || null,
          bd.querySelector("#m-grade").value);
        if (!res.ok) return toast(res.error);
        bd.remove();
        if (res.cancelled) return;
        go("documents");
      };
    });
};
