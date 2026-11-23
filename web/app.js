const fields = [
  ["internal_c", "Temperatura interna", "°C"],
  ["external_c", "Temperatura externa", "°C"],
  ["battery_v", "Tensão da bateria", "Vcc"],
  ["mains_v", "Tensão de entrada", "Vca RMS"],
];
const $ = (id) => document.getElementById(id);
const time = new Intl.DateTimeFormat("pt-BR", {
  dateStyle: "short", timeStyle: "medium", timeZone: "America/Belem",
});
const number = new Intl.NumberFormat("pt-BR", {maximumFractionDigits: 2});
let token = "";
let readings = [];
let requestId = 0;
let chosenDevice = "";

function chart(field, data) {
  const canvas = document.createElement("canvas");
  canvas.width = 620;
  canvas.height = 140;
  canvas.setAttribute("aria-label", "Histórico da leitura");
  const points = data.map(row => [Date.parse(row.measured_at), row[field]]);
  const values = points.filter(([, v]) => v !== null).map(([, v]) => v);
  if (!values.length) return canvas;
  const ctx = canvas.getContext("2d");
  const low = Math.min(...values), high = Math.max(...values);
  const padding = Math.max((high - low) * .1, .1);
  const start = points[0][0], span = points.at(-1)[0] - start;
  ctx.strokeStyle = "#29d7bc";
  ctx.fillStyle = ctx.strokeStyle;
  ctx.lineWidth = 2;
  ctx.beginPath();
  let previous = false;
  for (const [stamp, value] of points) {
    if (value === null) {
      previous = false;
      continue;
    }
    const x = span ? 5 + (stamp - start) / span * 610 : 310;
    const y = 130 - (value - low + padding) / (high - low + 2 * padding) * 120;
    if (previous) ctx.lineTo(x, y);
    else {
      ctx.moveTo(x, y);
      ctx.fillRect(x - 1, y - 1, 3, 3);
    }
    previous = true;
  }
  ctx.stroke();
  return canvas;
}

function render(data) {
  readings = data.readings;
  $("device").replaceChildren(...data.devices.map(id => new Option(id, id)));
  $("device").value = data.device;
  chosenDevice = data.device;
  const latest = readings.at(-1);
  $("download").disabled = !latest;
  $("status").textContent = latest?.demo ? "Dados de demonstração" : "Telemetria";
  $("updated").textContent = latest
    ? `Última medição: ${time.format(new Date(latest.measured_at))} · Último recebimento: ${time.format(new Date(data.last_received_at))} · Hora de Belém`
    : "Nenhuma leitura recebida.";
  $("cards").replaceChildren(...fields.map(([field, title, unit]) => {
    const section = document.createElement("article");
    const label = document.createElement("h2");
    label.textContent = title;
    const value = document.createElement("strong");
    value.textContent = latest?.[field] == null ? "Sem leitura" : `${number.format(latest[field])} ${unit}`;
    section.append(label, value, chart(field, readings));
    return section;
  }));
}

async function refresh() {
  if (!token) return;
  const id = ++requestId;
  const query = new URLSearchParams({limit: $("limit").value});
  if (chosenDevice) query.set("device", chosenDevice);
  try {
    const response = await fetch("/api/history?" + query, {
      headers: {Authorization: "Bearer " + token}, signal: AbortSignal.timeout(10000),
    });
    if (id !== requestId) return;
    if (!response.ok) {
      if (response.status === 401) {
        token = "";
        $("panel").hidden = true;
        $("access").hidden = false;
        $("status").textContent = "Token de leitura inválido.";
        return;
      }
      throw new Error();
    }
    const data = await response.json();
    if (id !== requestId) return;
    render(data);
    $("panel").hidden = false;
    $("access").hidden = true;
  } catch {
    if (id === requestId) $("status").textContent = "Servidor indisponível. Tentando novamente.";
  }
}

$("access").addEventListener("submit", (event) => {
  event.preventDefault();
  token = $("token").value.trim();
  $("token").value = "";
  refresh();
});
$("device").addEventListener("change", () => {
  chosenDevice = $("device").value;
  refresh();
});
$("limit").addEventListener("change", refresh);
$("download").addEventListener("click", () => {
  const keys = ["measured_at", "device", ...fields.map(([field]) => field), "demo", "errors"];
  const escape = (value) => '"' + String(value ?? "").replaceAll('"', '""') + '"';
  const lines = readings.map(row => keys.map(key => escape(
    key === "errors" ? JSON.stringify(row[key]) : row[key],
  )).join(","));
  const url = URL.createObjectURL(new Blob([[keys.join(","), ...lines].join("\n")], {type: "text/csv"}));
  const link = document.createElement("a");
  link.href = url;
  link.download = "leituras-" + (readings.at(-1)?.device || "gabinete") + ".csv";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
setInterval(refresh, 15000);
