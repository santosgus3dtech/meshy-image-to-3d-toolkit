import { createWriteStream, readFileSync } from "node:fs";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

const TERMINAL_STATUSES = new Set(["SUCCEEDED", "FAILED", "CANCELED", "EXPIRED"]);
const DEFAULT_API_BASE_URL = "https://api.meshy.ai/openapi/v1";

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
    throw new Error("Configure MESHY_API_KEY no arquivo .env antes de rodar o teste.");
  }

  const apiBaseUrl = trimTrailingSlash(process.env.MESHY_API_BASE_URL || DEFAULT_API_BASE_URL);
  const client = createMeshyClient({ apiBaseUrl, apiKey });

  if (options.status) {
    const task = await client.getTask(options.status);
    printTaskSummary(task);
    return;
  }

  if (!options.image && !options.inputTaskId) {
    throw new Error("Informe --image <arquivo-ou-url> ou --input-task-id <id>.");
  }

  const outputDir = path.resolve(options.out || "outputs");
  await mkdir(outputDir, { recursive: true });

  const payload = await buildCreatePayload(options);
  console.log("Criando tarefa image-to-3d na Meshy...");
  const taskId = await client.createTask(payload);
  console.log(`Tarefa criada: ${taskId}`);

  const task = await pollTask({
    client,
    taskId,
    pollIntervalMs: toPositiveInteger(options.pollIntervalMs, 10000),
    timeoutMs: toPositiveInteger(options.timeoutMs, 30 * 60 * 1000),
  });

  await saveTaskArtifacts({
    client,
    task,
    outputDir,
    requestedFormats: getTargetFormats(options),
  });
}

function createMeshyClient({ apiBaseUrl, apiKey }) {
  const headers = {
    Authorization: `Bearer ${apiKey}`,
    "Content-Type": "application/json",
  };

  return {
    async createTask(payload) {
      const response = await fetch(`${apiBaseUrl}/image-to-3d`, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });
      const data = await readJsonResponse(response);
      if (!data.result) {
        throw new Error(`Resposta inesperada ao criar tarefa: ${JSON.stringify(data)}`);
      }
      return data.result;
    },

    async getTask(taskId) {
      const response = await fetch(`${apiBaseUrl}/image-to-3d/${taskId}`, {
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

async function buildCreatePayload(options) {
  const payload = compactObject({
    input_task_id: options.inputTaskId,
    image_url: options.image ? await normalizeImageInput(options.image) : undefined,
    model_type: options.modelType,
    ai_model: options.aiModel || "latest",
    ultra_mode: options.ultra,
    should_texture: options.shouldTexture,
    enable_pbr: options.pbr,
    texture_resolution: options.textureResolution,
    texture_prompt: options.texturePrompt,
    texture_image_url: options.textureImage,
    should_remesh: options.remesh,
    target_polycount: options.targetPolycount ? Number(options.targetPolycount) : undefined,
    topology: options.topology,
    decimation_mode: options.decimationMode ? Number(options.decimationMode) : undefined,
    save_pre_remeshed_model: options.savePreRemeshedModel,
    pose_mode: options.poseMode,
    image_enhancement: options.imageEnhancement,
    remove_lighting: options.removeLighting,
    auto_size: options.autoSize,
    origin_at: options.originAt,
    alpha_thumbnail: options.alphaThumbnail,
    multi_view_thumbnails: options.multiViewThumbnails,
    target_formats: getTargetFormats(options),
  });

  if (payload.enable_pbr && payload.should_texture === false) {
    throw new Error("--pbr só pode ser usado quando textura está habilitada.");
  }

  return payload;
}

async function normalizeImageInput(image) {
  if (image.startsWith("http://") || image.startsWith("https://") || image.startsWith("data:")) {
    return image;
  }

  const imagePath = path.resolve(image);
  const stats = await stat(imagePath).catch(() => null);
  if (!stats?.isFile()) {
    throw new Error(`Imagem local não encontrada: ${imagePath}`);
  }

  const extension = path.extname(imagePath).toLowerCase();
  const mimeType = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
  }[extension];

  if (!mimeType) {
    throw new Error("A Meshy aceita imagem local apenas em .jpg, .jpeg ou .png.");
  }

  const buffer = await readFile(imagePath);
  return `data:${mimeType};base64,${buffer.toString("base64")}`;
}

async function pollTask({ client, taskId, pollIntervalMs, timeoutMs }) {
  const startedAt = Date.now();
  let lastProgress = null;

  while (true) {
    const task = await client.getTask(taskId);
    const progress = Number.isFinite(task.progress) ? `${task.progress}%` : "sem progresso informado";

    if (task.progress !== lastProgress || TERMINAL_STATUSES.has(task.status)) {
      console.log(`Status: ${task.status} (${progress})`);
      lastProgress = task.progress;
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

async function saveTaskArtifacts({ client, task, outputDir, requestedFormats }) {
  const taskJsonPath = path.join(outputDir, `${task.id}.json`);
  await writeFile(taskJsonPath, `${JSON.stringify(task, null, 2)}\n`, "utf8");
  console.log(`JSON da tarefa salvo em: ${taskJsonPath}`);

  const availableModelUrls = task.model_urls || {};
  const formatsToDownload = requestedFormats.filter((format) => availableModelUrls[format]);
  if (!formatsToDownload.length) {
    for (const [format, url] of Object.entries(availableModelUrls)) {
      if (url) {
        formatsToDownload.push(format);
      }
    }
  }

  if (!formatsToDownload.length) {
    throw new Error("Nenhum URL de modelo foi retornado pela Meshy.");
  }

  for (const format of formatsToDownload) {
    const modelPath = path.join(outputDir, `${task.id}.${format}`);
    await client.download(availableModelUrls[format], modelPath);
    console.log(`Modelo ${format} salvo em: ${modelPath}`);
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

    if (arg.startsWith("--no-")) {
      options[toCamelCase(arg.slice(5))] = false;
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

  return normalizeOptions(options);
}

function normalizeOptions(options) {
  return {
    ...options,
    format: options.format || "glb",
    pbr: options.pbr === true,
    remesh: options.remesh === true,
    ultra: options.ultra === true,
    autoSize: options.autoSize === true,
    alphaThumbnail: options.alphaThumbnail === true,
    multiViewThumbnails: options.multiViewThumbnails === true,
    savePreRemeshedModel: options.savePreRemeshedModel === true,
    shouldTexture: options.texture === false ? false : undefined,
    imageEnhancement: options.imageEnhancement === false ? false : undefined,
    removeLighting: options.removeLighting === true ? true : undefined,
  };
}

function getTargetFormats(options) {
  const formats = options.formats || options.format || "glb";
  return String(formats)
    .split(",")
    .map((format) => format.trim())
    .filter(Boolean);
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

function trimTrailingSlash(value) {
  return value.replace(/\/+$/, "");
}

function toCamelCase(value) {
  return value.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
}

function toPositiveInteger(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function printTaskSummary(task) {
  console.log(JSON.stringify(task, null, 2));
}

function printHelp() {
  console.log(`
Uso:
  npm run image-to-3d -- --image samples/input.png
  npm run image-to-3d -- --image "https://example.com/image.png"

Flags:
  --image <path|url|data-uri>       Imagem de entrada
  --input-task-id <id>              Usa resultado de uma tarefa de imagem Meshy
  --status <id>                     Mostra o JSON de uma tarefa existente
  --format <glb|obj|fbx|stl|usdz>   Formato de saída (padrão: glb)
  --out <dir>                       Pasta de saída (padrão: outputs)
  --pbr                             Gera mapas PBR
  --no-texture                      Pula geração de textura
  --texture-resolution <2k|4k|8k>   Resolução da textura
  --texture-prompt <texto>          Guia de textura
  --texture-image <url|data-uri>    Guia de textura por imagem
  --remesh                          Ativa remesh
  --target-polycount <numero>       Alvo de polígonos
  --topology <triangle|quad>        Topologia quando remesh é usado
  --decimation-mode <1|2|3|4>       Decimação adaptativa quando remesh é usado
  --save-pre-remeshed-model         Salva também o modelo antes do remesh
  --model-type <standard|smart-topology>
  --ai-model <latest|meshy-7|meshy-6|meshy-5|meshy-t2|meshy-t1>
  --formats <glb,3mf>               Múltiplos formatos de saída
  --remove-lighting                 Remove luz/sombra da textura quando suportado
  --ultra                           Ativa ultra mode quando suportado
  --poll-interval-ms <ms>           Intervalo de polling
  --timeout-ms <ms>                 Tempo máximo de espera
`);
}
