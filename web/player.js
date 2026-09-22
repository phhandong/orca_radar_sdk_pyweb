"use strict";

// Upper-bound search follows recording timestamps, including irregular intervals.
function frameAt(frames, time) {
  let lo = 0, hi = frames.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (frames[mid].time <= time) lo = mid + 1;
    else hi = mid;
  }
  return Math.max(0, lo - 1);
}

class Playback {
  constructor(frames) {
    this.frames = frames;
    this.duration = frames[frames.length - 1].time;
    this.position = 0;
    this.index = 0;
    this.speed = 1;
    this.playing = false;
    this.loop = false;
    this.last = 0;
  }
  tick(now) {
    if (this.playing) {
      this.position += Math.max(0, now - this.last) / 1000 * this.speed;
      if (this.position >= this.duration) {
        if (this.loop && this.duration > 0) this.position %= this.duration;
        else { this.position = this.duration; this.playing = false; }
      }
      this.index = frameAt(this.frames, this.position);
    }
    this.last = now;
  }
  seek(time, now) {
    this.position = Math.max(0, Math.min(this.duration, time));
    this.index = frameAt(this.frames, this.position);
    this.last = now;
  }
  toggle(now) {
    this.tick(now);
    if (!this.playing && this.position >= this.duration) this.seek(0, now);
    this.playing = !this.playing;
  }
  step(delta, now) {
    this.tick(now);
    this.playing = false;
    this.index = Math.max(0, Math.min(this.frames.length - 1, this.index + delta));
    this.position = this.frames[this.index].time;
  }
  setSpeed(speed, now) { this.tick(now); this.speed = speed; }
}

const FILTER_FIELDS = [
  { key: "velocity", label: "径向速度 (m/s)", column: 3, step: .01 },
  { key: "snr", label: "SNR (dB)", column: 4, step: .1 },
  { key: "noise", label: "Noise (dB)", column: 5, step: .1 },
];

// Side-info has 0.1 dB resolution; remove float32 representation noise at boundaries.
function filterValue(point, field) {
  const value = point[field.column];
  return field.column >= 4 ? Math.round(value * 10) / 10 : value;
}

function filterDomains(frames) {
  const domains = FILTER_FIELDS.map(() => [Infinity, -Infinity]);
  for (const frame of frames) for (const point of frame.points) {
    FILTER_FIELDS.forEach((field, i) => {
      const value = filterValue(point, field);
      if (!Number.isFinite(value)) throw new Error("点云缺少速度、SNR 或 Noise 数据，请重启播放器服务并刷新网页");
      domains[i][0] = Math.min(domains[i][0], value);
      domains[i][1] = Math.max(domains[i][1], value);
    });
  }
  return domains.map(([min, max], i) => {
    const step = FILTER_FIELDS[i].step;
    return min === Infinity ? [0, 0] : [
      Number((Math.floor(min / step + 1e-9) * step).toFixed(2)),
      Number((Math.ceil(max / step - 1e-9) * step).toFixed(2)),
    ];
  });
}

function matchesFilters(point, ranges) {
  return FILTER_FIELDS.every((field, i) =>
    filterValue(point, field) >= ranges[i][0] && filterValue(point, field) <= ranges[i][1]);
}

function clampFilterBound(range, domain, bound, value) {
  const next = [...range];
  if (Number.isFinite(value)) next[bound] = bound === 0
    ? Math.max(domain[0], Math.min(range[1], value))
    : Math.min(domain[1], Math.max(range[0], value));
  return next;
}

if (typeof module !== "undefined") module.exports = { Playback, frameAt, filterDomains, matchesFilters, clampFilterBound };
if (typeof document !== "undefined") startPlayer();

function startPlayer() {
  const $ = id => document.getElementById(id);
  const canvas = $("cloud"), ctx = canvas.getContext("2d");
  let recording = null, playback = null, busy = false, width = 1, height = 1;
  let yaw = -.65, pitch = .48, zoom = 1, drag = null, renderedIndex = -1;
  let domains = FILTER_FIELDS.map(() => [0, 0]), ranges = domains.map(pair => [...pair]), visibleCount = 0;
  let liveMode = false, livePaused = false, liveState = null, liveAction = false, liveSequence = null, initializing = true;
  let configOptions = [], configsLoading = false;
  const filterControls = FILTER_FIELDS.map((field, index) => {
    const group = document.createElement("fieldset");
    group.className = "filter-field";
    group.disabled = true;
    group.innerHTML = `<legend>${field.label}</legend>
      <div class="filter-values">
        <label>下限 <input id="${field.key}-min-value" type="number" step="${field.step}" aria-label="${field.label}下限数值"></label>
        <label>上限 <input id="${field.key}-max-value" type="number" step="${field.step}" aria-label="${field.label}上限数值"></label>
      </div>
      <div class="dual-range">
        <div class="range-track"><div class="range-selected"></div></div>
        <input id="${field.key}-min" type="range" step="${field.step}" aria-label="${field.label}下限">
        <input id="${field.key}-max" type="range" step="${field.step}" aria-label="${field.label}上限">
      </div><div class="filter-extents"><span></span><span></span></div>`;
    $("filter-fields").append(group);
    const sliders = [...group.querySelectorAll('input[type="range"]')];
    const numbers = [...group.querySelectorAll('input[type="number"]')];
    const rail = group.querySelector(".dual-range");
    const selection = group.querySelector(".range-selected");
    const extents = [...group.querySelectorAll(".filter-extents span")];
    function sync() {
      const [min, max] = domains[index], span = max - min;
      [0, 1].forEach(bound => {
        for (const input of [sliders[bound], numbers[bound]]) {
          input.min = min; input.max = max; input.value = ranges[index][bound];
        }
        extents[bound].textContent = domains[index][bound];
        sliders[bound].setAttribute("aria-valuetext", String(ranges[index][bound]));
      });
      const left = span ? (ranges[index][0] - min) / span * 100 : 0;
      const right = span ? (ranges[index][1] - min) / span * 100 : 100;
      selection.style.left = `${left}%`; selection.style.width = `${right - left}%`;
      group.disabled = busy || !recording || span === 0;
    }
    function change(bound, value) {
      ranges[index] = clampFilterBound(ranges[index], domains[index], bound, value);
      sync(); draw(); updateControls();
    }
    [0, 1].forEach(bound => {
      sliders[bound].oninput = () => change(bound, sliders[bound].valueAsNumber);
      numbers[bound].oninput = () => {
        // Keep partial numeric input editable (e.g. a minus sign).
        if (Number.isFinite(numbers[bound].valueAsNumber)) {
          ranges[index] = clampFilterBound(ranges[index], domains[index], bound, numbers[bound].valueAsNumber);
          sliders[bound].value = ranges[index][bound];
          const value = numbers[bound].value;
          sync(); numbers[bound].value = value;
          draw(); updateControls();
        }
      };
      numbers[bound].onchange = () => change(bound, numbers[bound].valueAsNumber);
      numbers[bound].onblur = sync;
    });
    let activeBound = null;
    const valueAt = event => {
      const rect = rail.getBoundingClientRect(), [min, max] = domains[index];
      const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left - 9) / Math.max(1, rect.width - 18)));
      return Number((Math.round((min + fraction * (max - min)) / field.step) * field.step).toFixed(2));
    };
    rail.onpointerdown = event => {
      if (group.disabled || event.button !== 0) return;
      event.preventDefault();
      const value = valueAt(event), [low, high] = ranges[index];
      activeBound = Math.abs(value - low) < Math.abs(value - high) ? 0 :
        Math.abs(value - low) > Math.abs(value - high) ? 1 : (value <= low ? 0 : 1);
      sliders[activeBound].focus();
      rail.setPointerCapture(event.pointerId);
      change(activeBound, value);
    };
    rail.onpointermove = event => { if (activeBound !== null) change(activeBound, valueAt(event)); };
    rail.onpointerup = rail.onpointercancel = rail.onlostpointercapture = () => { activeBound = null; };
    return { sync, group };
  });
  function resetFilters() {
    ranges = domains.map(pair => [...pair]);
    filterControls.forEach(control => control.sync());
    draw(); updateControls();
  }
  $("reset-filters").onclick = resetFilters;
  filterControls.forEach(control => control.sync());
  const formatTime = seconds => {
    const ms = Math.round(seconds * 1000);
    return `${String(Math.floor(ms / 60000)).padStart(2, "0")}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}.${String(ms % 1000).padStart(3, "0")}`;
  };
  function status(message, error = false) {
    $("status").textContent = message;
    $("status").classList.toggle("error", error);
  }
  function updateControls() {
    for (const id of ["play", "prev", "next"]) $(id).disabled = busy || !playback;
    $("seek").disabled = busy || !playback || playback.duration === 0;
    for (const id of ["files", "open", "refresh", "speed", "loop"]) $(id).disabled = busy;
    for (const id of ["files", "open", "refresh"]) $(id).disabled = busy || liveAction || initializing;
    if (liveMode) {
      for (const id of ["prev", "next", "seek", "speed", "loop"]) $(id).disabled = true;
      $("play").disabled = busy || !liveState?.running;
    }
    $("live-start").disabled = liveAction || busy || initializing || !liveState || liveState.running;
    if ($("live-configure").checked && (configsLoading || !configOptions.some(option => option.id === $("live-cfg").value))) {
      $("live-start").disabled = true;
    }
    $("live-stop").disabled = liveAction || !liveState?.running;
    $("live-view").disabled = busy || liveAction || initializing || !liveState?.running;
    for (const id of ["live-configure", "live-save"]) $(id).disabled = liveAction || Boolean(liveState?.running);
    $("live-cfg").disabled = liveAction || configsLoading || Boolean(liveState?.running) || !$("live-configure").checked;
    $("live-cfg-refresh").disabled = liveAction || configsLoading || Boolean(liveState?.running);
    $("reset-filters").disabled = busy || !recording;
    filterControls.forEach((control, i) => { control.group.disabled = busy || !recording || domains[i][0] === domains[i][1]; });
    if (!playback) return;
    if (liveMode) {
      const frame = recording.frames[0];
      $("play").textContent = livePaused ? "恢复实时" : "暂停画面";
      $("elapsed").textContent = "实时";
      $("duration").textContent = "—";
      $("seek").value = 0;
      $("stats").textContent = `实时帧 ${frame.sequence || 0} · 显示 ${visibleCount} / ${frame.points.length} 点`;
      $("timestamp").textContent = frame.sequence ? `接收时间 ${new Date(frame.timestamp * 1000).toLocaleString()} · ${frame.timestamp.toFixed(3)}` : "等待雷达数据";
      return;
    }
    $("play").textContent = playback.playing ? "暂停" : "播放";
    $("seek").value = playback.position;
    $("elapsed").textContent = formatTime(playback.position);
    const frame = recording.frames[playback.index];
    $("stats").textContent = `帧 ${playback.index + 1} / ${recording.frames.length} · 显示 ${visibleCount} / ${frame.points.length} 点`;
    $("timestamp").textContent = `接收时间 ${new Date(frame.timestamp * 1000).toLocaleString()} · ${frame.timestamp.toFixed(3)}`;
  }
  async function request(url, options) {
    const response = await fetch(url, options);
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || `请求失败 (${response.status})`);
    return value;
  }
  function configPreview() {
    if (!$("live-configure").checked) {
      $("live-cfg-preview").textContent = "未勾选下发配置：只接收数据，保留雷达当前配置。";
    } else {
      const selected = configOptions.find(option => option.id === $("live-cfg").value);
      $("live-cfg-preview").textContent = selected?.targets.length
        ? "开始接收时下发整份 CFG（包含文件内的启停命令）：" + selected.targets.map(target => `${target.radar} ← ${target.file}`).join("；")
        : "请选择可用配置文件";
    }
    updateControls();
  }
  async function refreshConfigs() {
    configsLoading = true; updateControls();
    try {
      const data = await request("/api/live/configs");
      const selected = $("live-cfg").value;
      configOptions = data.configs;
      $("live-cfg").replaceChildren(...configOptions.map(option => new Option(option.name, option.id)));
      $("live-cfg").value = configOptions.some(option => option.id === selected) ? selected : "default";
    } catch (error) {
      configOptions = [];
      status(`配置列表读取失败：${error.message}`, true);
    } finally { configsLoading = false; configPreview(); }
  }
  $("live-configure").onchange = configPreview;
  $("live-cfg").onchange = configPreview;
  $("live-cfg-refresh").onclick = refreshConfigs;
  function enterLive() {
    if (busy || !liveState) return;
    liveMode = true; livePaused = false; liveSequence = null;
    const frame = { timestamp: Date.now() / 1000, time: 0, sequence: 0, points: [] };
    recording = { name: "实时雷达", limits: liveState.limits, frames: [frame] };
    playback = new Playback(recording.frames);
    domains = [[-25, 25], [0, 100], [0, 100]];
    ranges = domains.map(pair => [...pair]);
    filterControls.forEach(control => control.sync());
    $("files").value = "";
    $("filename").textContent = `实时雷达 · ${$("live-radar").value}`;
    $("empty").hidden = true;
    status("等待最新雷达帧；实时模式不提供进度跳转和倍速");
    draw();
    displayLiveFrame();
  }
  function displayLiveFrame() {
    if (!liveMode || busy || livePaused || !liveState) return;
    const frame = liveState.frame;
    if (!frame || liveState.radar !== $("live-radar").value) return;
    const sequence = `${liveState.session}:${liveState.radar}:${frame.sequence}`;
    if (sequence === liveSequence) return;
    liveSequence = sequence;
    if (frame.points.length) {
      const incoming = filterDomains([frame]);
      let changed = false;
      domains.forEach((domain, i) => {
        [0, 1].forEach(bound => {
          const value = bound === 0 ? Math.min(domain[0], incoming[i][0]) : Math.max(domain[1], incoming[i][1]);
          if (value !== domain[bound]) {
            if (ranges[i][bound] === domain[bound]) ranges[i][bound] = value;
            domain[bound] = value; changed = true;
          }
        });
      });
      if (changed) filterControls.forEach(control => control.sync());
    }
    recording.frames[0] = frame;
    status("实时显示最新帧 · 筛选即时生效");
    draw();
  }
  function updateLiveState(data) {
    liveState = data;
    if (!$("live-radar").options.length) {
      for (const ip of data.radars) $("live-radar").add(new Option(ip, ip));
    }
    const age = data.frame ? (Date.now() / 1000 - data.frame.timestamp) : null;
    const state = data.running ? (data.configuring ? "正在下发配置" : age === null ? "等待数据" : age > 2 ? "超过 2 秒未收到新帧" : "正在接收") : "接收已停止";
    $("live-status").textContent = `${state} · UDP ${data.port} · 收到 ${data.counts[$("live-radar").value] || 0} 帧 · 无效帧 ${data.invalid}` +
      (data.save_path ? ` · 日志：${data.save_path}` : " · 未保存日志") + (data.error ? ` · ${data.error}` : "");
    $("live-status").classList.toggle("error", Boolean(data.error));
    $("live-cfg-result").textContent = data.config_targets?.length
      ? `本次会话配置${data.config_status}：` + data.config_targets.map(target => `${target.radar} ← ${target.file}`).join("；")
      : "本次会话未下发配置";
    updateControls();
    displayLiveFrame();
  }
  async function pollLive() {
    try {
      const radar = $("live-radar").value;
      const data = await request(`/api/live?radar=${encodeURIComponent(radar)}`);
      // A radar selection can change while this request is in flight.
      if (!radar || radar === $("live-radar").value) updateLiveState(data);
    } catch (error) {
      $("live-status").textContent = `实时服务连接失败：${error.message}`;
      $("live-status").classList.add("error");
    } finally { setTimeout(pollLive, 100); }
  }
  async function liveCommand(action) {
    if (liveAction || busy || initializing) return;
    liveAction = true; updateControls();
    try {
      const data = await request(`/api/live/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ configure: $("live-configure").checked, save: $("live-save").checked, config_id: $("live-cfg").value }),
      });
      updateLiveState(data);
      if (action === "start") enterLive();
    } catch (error) {
      status(`实时接收操作失败：${error.message}`, true);
    } finally { liveAction = false; updateControls(); }
  }
  $("live-start").onclick = () => liveCommand("start");
  $("live-stop").onclick = () => liveCommand("stop");
  $("live-view").onclick = enterLive;
  $("live-radar").onchange = () => { if (liveMode) enterLive(); };
  async function refresh(initial = false) {
    try {
      const config = await request("/api/files");
      const selected = $("files").value;
      $("files").replaceChildren(new Option("选择一个录制文件…", ""));
      for (const file of config.files) $("files").add(new Option(file.name, file.id));
      $("files").value = selected;
      if (initial) {
        if (![...$("speed").options].some(o => Number(o.value) === config.speed)) {
          $("speed").add(new Option(`${config.speed}×`, String(config.speed)));
        }
        $("speed").value = String(config.speed);
        $("loop").checked = config.loop;
        if (config.initial) { $("files").value = config.initial; await loadSelected(); }
      } else status(`已刷新，共 ${config.files.length} 个文件`);
    } catch (error) { status(error.message, true); }
  }
  async function load(url, options, name) {
    if (busy) return;
    busy = true;
    if (playback) { playback.tick(performance.now()); playback.playing = false; }
    updateControls();
    status("正在读取并解析点云…");
    try {
      const data = await request(url, options);
      const newDomains = filterDomains(data.frames);
      liveMode = false;
      recording = data;
      domains = newDomains;
      ranges = domains.map(pair => [...pair]);
      filterControls.forEach(control => control.sync());
      playback = new Playback(data.frames);
      playback.speed = Number($("speed").value);
      playback.loop = $("loop").checked;
      $("filename").textContent = name || data.name;
      $("seek").max = playback.duration;
      $("duration").textContent = formatTime(playback.duration);
      $("empty").hidden = true;
      renderedIndex = -1;
      status(`已加载 ${data.frames.length} 帧`);
      draw();
    } catch (error) { status(`打开失败：${error.message}`, true); }
    finally { busy = false; updateControls(); }
  }
  function loadSelected() {
    if (!$("files").value) return;
    return load("/api/load", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: $("files").value }) });
  }
  $("files").addEventListener("change", loadSelected);
  $("refresh").onclick = () => refresh();
  $("open").onclick = () => $("upload").click();
  $("upload").onchange = async () => {
    const file = $("upload").files[0];
    if (!file) return;
    if (file.size > 128 * 1024 * 1024) status("文件不能超过 128 MiB，请拆分日志后打开", true);
    else {
      $("files").value = "";
      await load("/api/upload", { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file }, file.name);
    }
    $("upload").value = "";
  };
  const toggle = () => {
    if (!playback || busy) return;
    if (liveMode) {
      if (!liveState?.running) return;
      livePaused = !livePaused;
      if (!livePaused) displayLiveFrame();
      status(livePaused ? "画面已暂停；后台继续接收和保存" : "实时显示最新帧");
    } else playback.toggle(performance.now());
    updateControls();
  };
  const step = delta => { if (playback && !busy && !liveMode) { playback.step(delta, performance.now()); updateControls(); draw(); } };
  $("play").onclick = toggle;
  $("prev").onclick = () => step(-1);
  $("next").onclick = () => step(1);
  $("seek").oninput = () => { if (!playback || busy) return; playback.seek(Number($("seek").value), performance.now()); updateControls(); draw(); };
  $("speed").onchange = () => { if (playback) playback.setSpeed(Number($("speed").value), performance.now()); };
  $("loop").onchange = () => { if (playback) playback.loop = $("loop").checked; };
  document.addEventListener("keydown", event => {
    if (event.target.closest("input, select, button, textarea") || event.ctrlKey || event.altKey || event.metaKey) return;
    if (["Space", "ArrowLeft", "ArrowRight"].includes(event.code)) {
      event.preventDefault();
      if (event.code === "Space") toggle();
      else step(event.code === "ArrowLeft" ? -1 : 1);
    }
  });

  function draw() {
    ctx.clearRect(0, 0, width, height);
    if (!recording) return;
    const limits = recording.limits;
    const center = [0, 2, 4].map(i => (limits[i] + limits[i + 1]) / 2);
    const scale = Math.min(width * .63, height * .7) / Math.max(...[0, 2, 4].map(i => limits[i + 1] - limits[i])) * zoom;
    const project = point => {
      const [x, y, z] = point.map((v, i) => v - center[i]);
      const rx = x * Math.cos(yaw) - y * Math.sin(yaw);
      const ry = x * Math.sin(yaw) + y * Math.cos(yaw);
      return [width / 2 + rx * scale, height / 2 + (ry * Math.sin(pitch) - z * Math.cos(pitch)) * scale,
        ry * Math.cos(pitch) + z * Math.sin(pitch)];
    };
    const line = (a, b, color, lineWidth = 1) => {
      const pa = project(a), pb = project(b);
      ctx.beginPath(); ctx.moveTo(pa[0], pa[1]); ctx.lineTo(pb[0], pb[1]);
      ctx.strokeStyle = color; ctx.lineWidth = lineWidth; ctx.stroke();
    };
    const base = [limits[0], limits[2], limits[4]];
    ctx.font = "11px system-ui";
    for (let i = 0; i <= 5; i++) {
      const x = limits[0] + (limits[1] - limits[0]) * i / 5;
      const y = limits[2] + (limits[3] - limits[2]) * i / 5;
      line([x, limits[2], limits[4]], [x, limits[3], limits[4]], "#23374a");
      line([limits[0], y, limits[4]], [limits[1], y, limits[4]], "#23374a");
    }
    ["X", "Y", "Z"].forEach((label, axis) => {
      const end = [...base]; end[axis] = limits[axis * 2 + 1];
      const color = ["#ef9098", "#68d5b1", "#7eaff6"][axis];
      line(base, end, color, 1.5);
      const p = project(end); ctx.fillStyle = color;
      ctx.fillText(`${label} ${end[axis].toFixed(1)} m`, p[0] + 8, p[1] - 8);
      const start = project(base);
      if (axis === 0) { ctx.fillStyle = "#7c93aa"; ctx.fillText(base.map(v => v.toFixed(1)).join(", ") + " m", start[0] + 6, start[1] + 16); }
    });
    const points = recording.frames[playback.index].points
      .filter(point => matchesFilters(point, ranges))
      .filter(point => point.slice(0, 3).every((v, i) => v >= limits[i * 2] && v <= limits[i * 2 + 1]))
      .map(point => ({ screen: project(point.slice(0, 3)), velocity: point[3] }))
      .sort((a, b) => b.screen[2] - a.screen[2]);
    for (const { screen, velocity } of points) {
      const hue = (1 - Math.max(0, Math.min(1, (velocity + 20) / 40))) * 240;
      ctx.fillStyle = `hsl(${hue} 85% 62%)`;
      ctx.beginPath(); ctx.arc(screen[0], screen[1], 2.8, 0, Math.PI * 2); ctx.fill();
    }
    if (!points.length) { ctx.fillStyle = "#899db4"; ctx.fillText("当前帧没有符合筛选条件且在视野范围内的目标点", 20, height - 24); }
    visibleCount = points.length;
    renderedIndex = playback.index;
    updateControls();
  }
  new ResizeObserver(() => {
    const rect = canvas.getBoundingClientRect(), ratio = window.devicePixelRatio || 1;
    width = rect.width; height = rect.height;
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0); draw();
  }).observe(canvas);
  canvas.onpointerdown = event => { drag = [event.clientX, event.clientY]; canvas.setPointerCapture(event.pointerId); };
  canvas.onpointermove = event => {
    if (!drag) return;
    yaw += (event.clientX - drag[0]) * .008;
    pitch = Math.max(-1.5, Math.min(1.5, pitch + (event.clientY - drag[1]) * .008));
    drag = [event.clientX, event.clientY]; draw();
  };
  canvas.onpointerup = canvas.onpointercancel = canvas.onlostpointercapture = () => { drag = null; };
  canvas.addEventListener("wheel", event => {
    event.preventDefault(); zoom = Math.max(.2, Math.min(8, zoom * Math.exp(-event.deltaY * .001))); draw();
  }, { passive: false });
  $("reset").onclick = () => { yaw = -.65; pitch = .48; zoom = 1; draw(); };
  function animate(now) {
    if (playback && playback.playing && !busy && !liveMode) {
      playback.tick(now); updateControls();
      if (renderedIndex !== playback.index) draw();
    }
    requestAnimationFrame(animate);
  }
  requestAnimationFrame(animate);
  refresh(true).finally(() => { initializing = false; updateControls(); });
  refreshConfigs();
  pollLive();
}
