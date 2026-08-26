import { createWriteStream, readFileSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

const DEFAULT_API_BASE_URL = "https://api.meshy.ai/openapi/v1";
const TERMINAL_STATUSES = new Set(["SUCCEEDED", "FAILED", "CANCELED"]);

main().catch((error) => {
  console.error(`\nErro: ${error.message}`);
  process.exitCode = 1;
});

async function main() {
  loadDotEnv();
  const options = parseArgs(process.argv.slice(2));

  if (options.help) {
    printHelp();
    return;
  }

  const apiKey = process.env.MESHY_API_KEY;
  if (!apiKey || apiKey === "msy_YOUR_API_KEY") {
    throw new Error("Configure MESHY_API_KEY no arquivo .env antes de rodar.");
  }

  const mode = options.mode;
  if (!["analyze", "multi-color", "repair"].includes(mode)) {
    throw new Error("Use --mode analyze, --mode multi-color ou --mode repair.");
  }
  if (!options.taskId && !options.modelUrl) {
    throw new Error("Informe --task-id <id> ou --model-url <url|data-uri>.");
  }

  const outputDir = path.resolve(options.out || `outputs/print-${mode}`);
  await mkdir(outputDir, { recursive: true });

  const client = createMeshyPrintClient({
    apiBaseUrl: trimTrailingSlash(process.env.MESHY_API_BASE_URL || DEFAULT_API_BASE_URL),
    apiKey,
  });

  const payload = compactObject({
    input_task_id: options.taskId,
    model_url: options.modelUrl,
    max_colors: options.maxColors ? Number(options.maxColors) : undefined,
    style: options.style,
    alpha_thumbnail: options.alphaThumbnail === true ? true : undefined,
  });

  console.log(`Criando tarefa print/${mode}...`);
  const taskId = await client.createTask(mode, payload);
  console.log(`Tarefa criada: ${taskId}`);

  const task = await pollTask({
    client,
    mode,
    taskId,
    pollIntervalMs: toPositiveInteger(options.pollIntervalMs, 5000),
    timeoutMs: toPositiveInteger(options.timeoutMs, 30 * 60 * 1000),
  });

  await saveTask(task, outputDir);
  await downloadOutputs({ client, task, outputDir });
}

function createMeshyPrintClient({ apiBaseUrl, apiKey }) {
  const headers = {
    Authorization: `Bearer ${apiKey}`,
    "Content-Type": "application/json",
  };

  return {
    async createTask(mode, payload) {
      const response = await fetch(`${apiBaseUrl}/print/${mode}`, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });
      const data = await readJsonResponse(response);
      if (!data.result) {
        throw new Error(`Resposta inesperada: ${JSON.stringify(data)}`);
      }
      return data.result;
    },

    async getTask(mode, taskId) {
      const response = await fetch(`${apiBaseUrl}/print/${mode}/${taskId}`, {
        headers: { Authorization: `Bearer ${apiKey}` },
      });
      return readJsonResponse(response);
    },

    async download(url, destination) {
      const response = await fetch(url);
      if (!response.ok) {
        const body = await response.text();
        throw new Error(`Falha ao baixar ${url}: HTTP ${response.status} ${body}`);
      }
      await pipeline(Readable.fromWeb(response.body), createWriteStream(destination));
    },
  };
}

async function pollTask({ client, mode, taskId, pollIntervalMs, timeoutMs }) {
  const startedAt = Date.now();
  let lastStatusLine = "";

  while (true) {
    const task = await client.getTask(mode, taskId);
    const progress = Number.isFinite(task.progress) ? `${task.progress}%` : "sem progresso";
    const statusLine = `${task.status} (${progress})`;

    if (statusLine !== lastStatusLine || TERMINAL_STATUSES.has(task.status)) {
      console.log(`Status: ${statusLine}`);
      lastStatusLine = statusLine;
    }

    if (task.status === "SUCCEEDED") {
      return task;
    }

    if (TERMINAL_STATUSES.has(task.status)) {
      const message = task.task_error?.message ? `: ${task.task_error.message}` : "";
      throw new Error(`Tarefa terminou com status ${task.status}${message}`);
    }

    if (Date.now() - startedAt > timeoutMs) {
      throw new Error(`Tempo limite excedido aguardando a tarefa ${taskId}.`);
    }

    await sleep(pollIntervalMs);
  }
}

async function saveTask(task, outputDir) {
  const taskPath = path.join(outputDir, `${task.id}.json`);
  await writeFile(taskPath, `${JSON.stringify(task, null, 2)}\n`, "utf8");
  console.log(`JSON salvo em: ${taskPath}`);
}

async function downloadOutputs({ client, task, outputDir }) {
  const modelUrls = Object.entries(task.model_urls || {}).filter(([, url]) => url);
  for (const [format, url] of modelUrls) {
    const filePath = path.join(outputDir, `${task.id}.${format}`);
    await client.download(url, filePath);
    console.log(`Modelo ${format} salvo em: ${filePath}`);
  }

  if (task.thumbnail_url) {
    const thumbnailPath = path.join(outputDir, `${task.id}-thumbnail.png`);
    await client.download(task.thumbnail_url, thumbnailPath);
    console.log(`Thumbnail salva em: ${thumbnailPath}`);
  }
}

function parseArgs(args) {
  const options = {};

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--help" || arg === "-h") {
      options.help = true;
      continue;
    }
    if (!arg.startsWith("--")) {
      throw new Error(`Argumento inesperado: ${arg}`);
    }

    const key = toCamelCase(arg.slice(2));
    const next = args[index + 1];
    if (!next || next.startsWith("--")) {
      options[key] = true;
      continue;
    }

    options[key] = next;
    index += 1;
  }

  return options;
}

function loadDotEnv() {
  const envPath = path.resolve(".env");
  let contents;

  try {
    contents = readFileSync(envPath, "utf8");
  } catch {
    return;
  }

  for (const line of contents.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }

    const separatorIndex = trimmed.indexOf("=");
    if (separatorIndex === -1) {
      continue;
    }

    const key = trimmed.slice(0, separatorIndex).trim();
    const rawValue = trimmed.slice(separatorIndex + 1).trim();
    const value = rawValue.replace(/^["']|["']$/g, "");

    if (key && process.env[key] === undefined) {
      process.env[key] = value;
    }
  }
}

async function readJsonResponse(response) {
  const text = await response.text();
  let data;

  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`Resposta não-JSON da Meshy: HTTP ${response.status} ${text}`);
  }

  if (!response.ok) {
    throw new Error(`Meshy API retornou HTTP ${response.status}: ${JSON.stringify(data)}`);
  }

  return data;
}

function compactObject(object) {
  return Object.fromEntries(Object.entries(object).filter(([, value]) => value !== undefined && value !== ""));
}

function toCamelCase(value) {
  return value.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
}

function trimTrailingSlash(value) {
  return value.replace(/\/+$/, "");
}

function toPositiveInteger(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function printHelp() {
  console.log(`
Uso:
  npm run meshy-print -- --mode analyze --task-id <task-id>
  npm run meshy-print -- --mode multi-color --task-id <task-id> --max-colors 4 --style cartoon
  npm run meshy-print -- --mode repair --task-id <task-id>

Flags:
  --mode <analyze|multi-color|repair>
  --task-id <id>                 Usa uma tarefa Meshy existente
  --model-url <url|data-uri>     Usa uma URL/Data URI de modelo
  --max-colors <1-16>            Para multi-color
  --style <cartoon|realistic>    Para multi-color
  --out <dir>                    Pasta de saída
`);
}
