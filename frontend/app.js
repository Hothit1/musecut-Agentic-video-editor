/* MuseCut frontend: upload page + plan editor (vanilla JS). */
const MuseCut = (() => {
  const $ = (s) => document.querySelector(s);
  const api = async (url, opts = {}) => {
    const r = await fetch(url, opts);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
    return j;
  };
  const fmt = (t) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---------------- upload ----------------
  function initUpload() {
    const file = $("#file"), drop = $("#drop"), go = $("#go"), msg = $("#msg");
    let chosen = null;
    const pick = (f) => { chosen = f; drop.classList.add("has");
      $("#dropText").textContent = `${f.name} · ${(f.size / 1e6).toFixed(0)} MB`; go.disabled = false; };
    drop.onclick = () => file.click();
    file.onchange = () => file.files[0] && pick(file.files[0]);
    drop.ondragover = (e) => { e.preventDefault(); };
    drop.ondrop = (e) => { e.preventDefault(); e.dataTransfer.files[0] && pick(e.dataTransfer.files[0]); };
    const mark = (k, state) => document.querySelectorAll(".step").forEach((el) => {
      if (el.dataset.k === k) { el.className = "step " + state; el.firstChild.textContent = state === "done" ? "✓ " : "● "; }
    });
    go.onclick = async () => {
      go.disabled = true; $("#steps").hidden = false; msg.hidden = true;
      try {
        mark("upload", "on");
        const fd = new FormData();
        fd.append("video", chosen); fd.append("prompt", $("#prompt").value); fd.append("target_duration_sec", $("#target").value);
        if (+$("#manual").value > 0) fd.append("manual_edit_minutes", Math.round(+$("#manual").value));
        const p = await api("/api/projects", { method: "POST", body: fd });
        mark("upload", "done");
        await api(`/api/projects/${p.project_id}/plan`, { method: "POST" });
        const order = ["transcrib", "planning", "face", "Muse Image"];
        for (;;) {
          await sleep(2000);
          const st = await api(`/api/projects/${p.project_id}/plan`);
          const prog = (st.job && st.job.progress) || "";
          const i = order.findIndex((k) => prog.includes(k));
          order.forEach((k, j) => mark(k, j < i ? "done" : j === i ? "on" : ""));
          if (st.job && st.job.status === "failed") throw new Error(st.job.error);
          if (st.plan) { order.forEach((k) => mark(k, "done")); location.href = `plan-editor.html?id=${p.project_id}`; return; }
        }
      } catch (e) { msg.hidden = false; msg.className = "status err"; msg.textContent = e.message; go.disabled = false; }
    };
  }

  // ---------------- editor ----------------
  let pid, plan, tracks, project, saving = Promise.resolve(), playQueue = null;

  async function initEditor() {
    pid = new URLSearchParams(location.search).get("id");
    project = await api(`/api/projects/${pid}`);
    $("#projSub").textContent = `${pid} · ${project.prompt}`;
    const v = $("#src");
    v.src = project.video_url;
    v.addEventListener("timeupdate", onTime);
    v.addEventListener("seeked", onTime);
    await load();
    $("#approve").onclick = approve;
    $("#playAll").onclick = playAll;
    $("#stop").onclick = () => { playQueue = null; v.pause(); };
    requestAnimationFrame(drawLoop);
    const r = await api(`/api/projects/${pid}/render`);
    if (r.status !== "not_started") showRender();
  }

  async function load(data) {
    const d = data || (await api(`/api/projects/${pid}/plan`));
    if (!d.plan) { $("#loading").textContent = "Plan not ready: " + JSON.stringify(d.job); return; }
    plan = d.plan; tracks = d.tracks;
    $("#loading").hidden = true; $("#editor").hidden = false;
    renderAll(d.kept_duration_sec);
  }

  function locked() { return plan.status === "approved"; }

  function renderAll(kept) {
    const L = locked();
    $("#approve").disabled = L;
    $("#approve").textContent = L ? "Approved ✓ (locked)" : "Approve & Render";
    $("#title").value = plan.title; $("#hook").value = plan.hook_text || "";
    $("#post").value = plan.post_copy; $("#tags").value = (plan.hashtags || []).map((h) => "#" + h).join(" ");
    $("#target").value = plan.target_duration_sec;
    $("#musicOn").checked = plan.music.enabled; $("#musicVol").value = plan.music.volume;
    $("#musicVolTxt").textContent = Math.round(plan.music.volume * 100) + "%";
    for (const id of ["#title", "#hook", "#post", "#tags", "#target", "#musicOn", "#musicVol"]) $(id).disabled = L;
    $("#title").onchange = () => patch({ title: $("#title").value });
    $("#hook").onchange = () => patch({ hook_text: $("#hook").value });
    $("#post").onchange = () => patch({ post_copy: $("#post").value });
    $("#tags").onchange = () => patch({ hashtags: $("#tags").value.split(/[\s,]+/).map((x) => x.replace(/^#/, "")).filter(Boolean) });
    $("#target").onchange = () => patch({ target_duration_sec: +$("#target").value });
    $("#musicOn").onchange = () => patch({ music: { enabled: $("#musicOn").checked } });
    $("#musicVol").oninput = () => ($("#musicVolTxt").textContent = Math.round($("#musicVol").value * 100) + "%");
    $("#musicVol").onchange = () => patch({ music: { volume: +$("#musicVol").value } });

    $("#thumbs").innerHTML = plan.thumbnail_options.map((t) => `
      <label title="${t.style} · ${t.source}"><input type="radio" name="thumb" value="${t.id}" ${t.id === plan.thumbnail_selected ? "checked" : ""} ${L ? "disabled" : ""}>
      <img src="${t.url}"><div class="small muted" style="text-align:center">${t.style}${t.source !== "muse_image" ? " (fallback)" : ""}</div></label>`).join("");
    document.querySelectorAll("input[name=thumb]").forEach((r) => (r.onchange = () => patch({ thumbnail_selected: r.value })));
    $("#chapters").innerHTML = (plan.chapters || []).map((c) => `<li data-t="${c.start_sec}">${fmt(c.start_sec)} — ${esc(c.title)}</li>`).join("");
    document.querySelectorAll("#chapters li").forEach((li) => (li.onclick = () => seek(+li.dataset.t)));

    const total = kept ?? plan.segments.filter((s) => s.keep).reduce((a, s) => a + s.end_sec - s.start_sec, 0);
    $("#dur").innerHTML = `Kept: <b class="${total > plan.target_duration_sec * 1.05 ? "over" : ""}">${total.toFixed(1)} s</b> (before pause trimming)`;
    const keptSegs = plan.segments.filter((s) => s.keep);
    const colors = { hook: "#6b5200", main: "#2a3f78", closing: "#1d5a42" };
    $("#timeline").innerHTML = keptSegs.map((s) => `<div data-id="${s.id}" style="flex:${s.end_sec - s.start_sec};background:${s.shot_type === "b_roll" ? "#33427a" : colors[s.role]}">${s.id.replace("seg_", "")}</div>`).join("");
    document.querySelectorAll("#timeline div").forEach((d) => (d.onclick = () => playSeg(d.dataset.id)));

    $("#segs").innerHTML = plan.segments.map((s, i) => segHtml(s, i, L)).join("");
    bindSegs();
  }

  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  function segHtml(s, i, L) {
    const caps = plan.captions.filter((c) => c.segment_id === s.id);
    const src = s.shot_type === "a_roll" ? "a_roll" : s.b_roll_source;
    return `<div class="seg ${s.keep ? "" : "off"}" data-id="${s.id}">
      <div class="head">
        <input type="checkbox" class="keep" ${s.keep ? "checked" : ""} ${L ? "disabled" : ""} title="keep">
        <b>${s.id}</b><span class="pill ${s.role}">${s.role}</span>
        ${s.shot_type === "b_roll" ? `<span class="pill broll">B-roll · ${s.b_roll_source === "same_video" ? "same video" : "Muse Image"}</span>` : `<span class="pill">A-roll · ${s.layout === "face" ? "face crop" : "full frame"}</span>`}
        <span class="t">${fmt(s.start_sec)} → ${fmt(s.end_sec)} · ${(s.end_sec - s.start_sec).toFixed(1)}s · score ${s.score}</span>
        <span style="flex:1"></span>
        <button class="play" title="preview">▶</button>
        <button class="up" ${L || i === 0 ? "disabled" : ""}>↑</button><button class="down" ${L || i === plan.segments.length - 1 ? "disabled" : ""}>↓</button>
      </div>
      <div class="reason">${esc(s.reason)}</div>
      ${s.auto_note ? `<div class="note">⚙ ${esc(s.auto_note)}</div>` : ""}
      <div class="broll">
        <select class="shot" style="width:auto" ${L ? "disabled" : ""}>
          <option value="a_roll" ${src === "a_roll" ? "selected" : ""}>A-roll (speaker)</option>
          <option value="same_video" ${src === "same_video" ? "selected" : ""}>B-roll: same video (full frame)</option>
          <option value="generated_image" ${src === "generated_image" ? "selected" : ""}>B-roll: Muse Image + Ken Burns</option>
        </select>
        ${s.shot_type === "b_roll" ? `<input type="text" class="bdesc" value="${esc(s.b_roll_description)}" placeholder="describe the B-roll image" ${L ? "disabled" : ""}>` : ""}
        ${s.b_roll_url ? `<img src="${s.b_roll_url}">` : ""}
      </div>
      ${caps.map((c) => `<div class="cap"><textarea data-cap="${c.id}" rows="1" ${L ? "disabled" : ""}>${esc(c.text)}</textarea></div>`).join("")}
    </div>`;
  }

  function segPayload() {
    return plan.segments.map((s) => ({ id: s.id, keep: s.keep, role: s.role, shot_type: s.shot_type,
      b_roll_source: s.b_roll_source, b_roll_description: s.b_roll_description }));
  }

  function bindSegs() {
    document.querySelectorAll(".seg").forEach((el) => {
      const id = el.dataset.id, s = plan.segments.find((x) => x.id === id);
      const idx = plan.segments.indexOf(s);
      el.querySelector(".keep").onchange = (e) => { s.keep = e.target.checked; patch({ segments: segPayload() }); };
      el.querySelector(".play").onclick = () => playSeg(id);
      el.querySelector(".up").onclick = () => move(idx, -1);
      el.querySelector(".down").onclick = () => move(idx, 1);
      el.querySelector(".shot").onchange = (e) => {
        const v = e.target.value;
        if (v === "a_roll") { s.shot_type = "a_roll"; s.b_roll_source = null; s.b_roll_description = null; }
        else { s.shot_type = "b_roll"; s.b_roll_source = v;
          s.b_roll_description = s.b_roll_description || (v === "same_video" ? "Full frame from the source video" : "Illustration of: " + s.transcript.slice(0, 80)); }
        patch({ segments: segPayload() });
      };
      const bd = el.querySelector(".bdesc");
      if (bd) bd.onchange = () => { s.b_roll_description = bd.value; patch({ segments: segPayload() }); };
      el.querySelectorAll("textarea[data-cap]").forEach((t) => (t.onchange = () => patch({ captions: [{ id: t.dataset.cap, text: t.value }] })));
    });
  }

  function move(i, d) {
    const a = plan.segments; [a[i], a[i + d]] = [a[i + d], a[i]];
    patch({ segments: segPayload() });
  }

  function patch(body) {
    $("#saveState").textContent = "saving…";
    saving = saving.then(async () => {
      try {
        const d = await api(`/api/projects/${pid}/plan`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        await load(d);
        $("#saveState").textContent = "saved ✓";
      } catch (e) { $("#saveState").textContent = "error: " + e.message; await load(); }
    });
    return saving;
  }

  // ---------- preview ----------
  let curSeg = null;
  function seek(t) { const v = $("#src"); v.currentTime = t; }
  function playSeg(id) {
    const s = plan.segments.find((x) => x.id === id);
    playQueue = [s]; startQueue();
  }
  function playAll() { playQueue = plan.segments.filter((s) => s.keep); startQueue(); }
  function startQueue() {
    const v = $("#src");
    if (!playQueue || !playQueue.length) { v.pause(); curSeg = null; return; }
    curSeg = playQueue.shift();
    v.muted = false; v.currentTime = curSeg.start_sec; v.play();
    document.querySelectorAll(".seg").forEach((el) => el.classList.toggle("playing", el.dataset.id === curSeg.id));
    $("#nowSeg").textContent = `Playing ${curSeg.id} (${curSeg.role})`;
  }
  function onTime() {
    const v = $("#src");
    if (curSeg && v.currentTime >= curSeg.end_sec) { if (playQueue) startQueue(); else { v.pause(); curSeg = null; } }
  }
  function segAt(t) { return plan.segments.find((s) => t >= s.start_sec && t <= s.end_sec); }
  function cropX(track, rel) {
    const k = track.crop_keys; if (!k || !k.length) return null;
    if (rel <= k[0][0]) return k[0][1];
    for (let i = 1; i < k.length; i++) if (rel <= k[i][0]) { const [t0, x0] = k[i - 1], [t1, x1] = k[i]; return x0 + (x1 - x0) * (rel - t0) / Math.max(t1 - t0, 1e-6); }
    return k[k.length - 1][1];
  }
  function capAt(t) { return plan.captions.find((c) => t >= c.start_sec && t <= c.end_sec); }
  function drawLoop() {
    requestAnimationFrame(drawLoop);
    const v = $("#src"), ov = $("#overlay"), vc = $("#vert");
    if (!plan || !v.videoWidth) return;
    const W = v.videoWidth, H = v.videoHeight;
    ov.width = ov.clientWidth; ov.height = ov.clientHeight;
    const g = ov.getContext("2d"), sx = ov.width / W, sy = ov.height / H;
    g.clearRect(0, 0, ov.width, ov.height);
    const t = v.currentTime, s = segAt(t), tr = s && tracks[s.id];
    const vg = vc.getContext("2d");
    vg.fillStyle = "#000"; vg.fillRect(0, 0, vc.width, vc.height);
    let layout = s ? (s.shot_type === "b_roll" ? (s.b_roll_source === "same_video" ? "slide" : "image") : (tr && tr.layout) || "slide") : "slide";
    if (layout === "face" && tr) {
      const x = cropX(tr, t - s.start_sec), cw = tr.crop_w;
      g.fillStyle = "#0009"; g.fillRect(0, 0, x * sx, ov.height); g.fillRect((x + cw) * sx, 0, ov.width, ov.height);
      g.strokeStyle = "#ffd400"; g.lineWidth = 2; g.strokeRect(x * sx, 1, cw * sx, ov.height - 2);
      vg.drawImage(v, x, 0, cw, H, 0, 0, vc.width, vc.height);
    } else if (layout === "image" && s.b_roll_url) {
      if (!drawLoop.img || drawLoop.img.dataset.src !== s.b_roll_url) { drawLoop.img = new Image(); drawLoop.img.src = s.b_roll_url; drawLoop.img.dataset.src = s.b_roll_url; }
      if (drawLoop.img.complete) vg.drawImage(drawLoop.img, 0, 0, vc.width, vc.height);
    } else {
      vg.filter = "blur(8px) brightness(.8)"; vg.drawImage(v, (W - H * 9 / 16) / 2, 0, H * 9 / 16, H, 0, 0, vc.width, vc.height); vg.filter = "none";
      const fh = vc.width * H / W; vg.drawImage(v, 0, (vc.height - fh) / 2, vc.width, fh);
    }
    const c = capAt(t);
    if (c && s && s.keep) {
      vg.font = "bold 20px Impact, sans-serif"; vg.textAlign = "center"; vg.lineWidth = 4; vg.strokeStyle = "#000"; vg.fillStyle = "#fff";
      const words = c.text.split(/\s+/), frac = (t - c.start_sec) / Math.max(c.end_sec - c.start_sec, 0.1);
      const wi = Math.min(words.length - 1, Math.floor(frac * words.length)), g0 = Math.floor(wi / 3) * 3;
      const line = words.slice(g0, g0 + 3).join(" ").toUpperCase();
      vg.strokeText(line, vc.width / 2, vc.height * 0.72); vg.fillText(line, vc.width / 2, vc.height * 0.72);
    }
    if (s && !s.keep) { vg.fillStyle = "#000a"; vg.fillRect(0, 0, vc.width, vc.height); vg.fillStyle = "#fff"; vg.font = "14px sans-serif"; vg.textAlign = "center"; vg.fillText("dropped segment", vc.width / 2, vc.height / 2); }
  }

  // ---------- approve / render ----------
  async function approve() {
    if (!confirm("Approve locks the plan and starts the final render. Continue?")) return;
    await saving;
    try { await api(`/api/projects/${pid}/approve`, { method: "POST" }); await load(); showRender(); }
    catch (e) { alert(e.message); }
  }
  async function showRender() {
    $("#resultWrap").hidden = false;
    const st = $("#renderStatus");
    for (;;) {
      const r = await api(`/api/projects/${pid}/render`);
      st.className = "status" + (r.status === "failed" ? " err" : "");
      st.textContent = `Render: ${r.status}${r.progress ? " · " + r.progress : ""}${r.error ? " · " + r.error : ""}`;
      if (r.status === "done") break;
      if (r.status === "failed") return;
      await sleep(2000);
    }
    const e = await api(`/api/projects/${pid}/export`);
    const rows = Object.entries(e.cost_summary.by_model).map(([m, v]) => `<tr><td>${m}</td><td>${v.calls}</td><td>${v.failed}</td><td>$${v.usd.toFixed(4)}</td></tr>`).join("");
    $("#result").hidden = false;
    $("#result").innerHTML = `
      <div><video src="${e.mp4}" controls playsinline></video></div>
      <div>
        <div class="row"><a href="${e.mp4}" download>⬇ MP4 (${e.duration_sec.toFixed(1)} s, 1080×1920)</a> · <a href="${e.srt}" download>SRT</a> · <a href="${e.thumbnail}" download>Thumbnail PNG</a> · <a href="${e.plan_json}" download>Plan JSON</a> · <a href="${e.cost_log}" download>Cost log</a></div>
        <h2 style="margin-top:16px">Post copy</h2><textarea rows="5" readonly>${esc(e.post_copy)}</textarea>
        <h2 style="margin-top:16px">Meta models used</h2>
        <table><tr><th>Model</th><th>Calls</th><th>Failed/retried</th><th>Est. cost</th></tr>${rows}
        <tr><td><b>Total</b></td><td></td><td></td><td><b>$${e.cost_summary.total_usd.toFixed(4)}</b></td></tr></table>
        ${timeSaved(e.time_saved)}
        ${e.render_notes.length ? `<h2 style="margin-top:16px">Render log</h2><ul class="small">${e.render_notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
      </div>`;
  }

  function timeSaved(t) {
    if (!t) return "";
    const m = (s) => (s / 60).toFixed(1) + " min", st = t.stages;
    const head = t.manual_minutes
      ? `<div class="row" style="gap:28px;margin:6px 0 10px"><div><div class="small muted">By hand (${t.manual_source})</div><div style="font-size:28px;font-weight:800">${t.manual_minutes} min</div></div>
         <div><div class="small muted">With MuseCut (measured, incl. your review)</div><div style="font-size:28px;font-weight:800;color:var(--ok)">${m(t.total_s)}</div></div>
         <div><div class="small muted">Saved</div><div style="font-size:28px;font-weight:800;color:var(--accent2)">${t.saved_minutes} min · ${t.speedup_x}× faster</div></div></div>` : "";
    return `<h2 style="margin-top:16px">Time saved</h2>${head}
      <table><tr><th>Step</th><th>Measured</th></tr>
      <tr><td>Transcribe (Muse Voice)</td><td>${m(st.transcribe_s)}</td></tr>
      <tr><td>Edit Plan (Muse Spark)</td><td>${m(st.spark_plan_s)}</td></tr>
      <tr><td>Face tracking (SAM 3.1)</td><td>${m(st.sam_tracking_s)}</td></tr>
      <tr><td>Thumbnails + B-roll (Muse Image)</td><td>${m(st.muse_image_s)}</td></tr>
      <tr><td>Your review in the plan editor</td><td>${m(st.human_review_s)}</td></tr>
      <tr><td>Render (FFmpeg)</td><td>${m(st.render_s)}</td></tr>
      <tr><td><b>Total</b></td><td><b>${m(t.total_s)}</b></td></tr></table>`;
  }

  return { initUpload, initEditor };
})();
