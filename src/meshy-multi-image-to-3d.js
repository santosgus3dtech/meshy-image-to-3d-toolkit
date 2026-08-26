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

  const images = parseList(options.images);
  if (!images.length && !options.inputTaskId) {
    throw new Error("Informe --images <img1,img2,...> ou --input-task-id <id>.");
  }
  if (images.length > 4) {
    throw new Error("A API multi-image aceita no máximo 4 imagens.");
  }

  const outputDir = path.resolve(options.out || "outputs/multi-image-to-3d");
  await mkdir(outputDir, { recursive: true });

  const client = createMeshyClient({
    apiBaseUrl: trimTrailingSlash(process.env.MESHY_API_BASE_URL || DEFAULT_API_BASE_URL),
    apiKey,
  });

  const payload = await buildPayload(options, images);
  console.log(`Criando tarefa multi-image-to-3d com ${images.length || 1} entrada(s)...`);
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
      const response = await fetch(`${apiBaseUrl}/multi-image-to-3d`, {
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
      const response = await fetch(`${apiBaseUrl}/multi-image-to-3d/${taskId}`, {
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

async function buildPayload(options, images) {
  const imageUrls = [];
  for (const image of images) {
    imageUrls.push(await normalizeImageInput(image));
  }

  return compactObject({
    input_task_id: options.inputTaskId,
    image_urls: imageUrls.length ? imageUrls : undefined,
    ai_model: options.aiModel || "latest",
    should_texture: options.texture === false ? false : true,
    enable_pbr: options.pbr === true,
    texture_resolution: options.textureResolution || "2k",
    texture_prompt: options.texturePrompt,
    texture_image_urls: options.textureImages ? await normalizeImageInputs(parseList(options.textureImages)) : undefined,
    should_remesh: options.remesh === false ? false : options.remesh === true ? true : undefined,
    topology: options.topology,
    target_polycount: options.targetPolycount ? Number(options.targetPolycount) : undefined,
    decimation_mode: options.decimationMode ? Number(options.decimationMode) : undefined,
    save_pre_remeshed_model: options.savePreRemeshedModel === true,
    pose_mode: options.poseMode,
    image_enhancement: options.imageEnhancement === false ? false : undefined,
    remove_lighting: options.removeLighting === false ? false : true,
    moderation: options.moderation === true,
    target_formats: getTargetFormats(options),
    auto_size: options.autoSize === true,
    alpha_thumbnail: options.alphaThumbnail === true,
    multi_view_thumbnails: options.multiViewThumbnails === true,
    origin_at: options.originAt,
  });
}

async function normalizeImageInputs(images) {
  const normalized = [];
  for (const image of images) {
    normalized.push(await normalizeImageInput(image));
  }
  return normalized;
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
  for (const [format, url] of Object.entries(availableModelUrls)) {
    if (url && !formatsToDownload.includes(format)) {
      formatsToDownload.push(format);
    }
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

  if (task.alpha_thumbnail_url) {
    const thumbnailPath = path.join(outputDir, `${task.id}-thumbnail-alpha.png`);
    await client.download(task.alpha_thumbnail_url, thumbnailPath);
    console.log(`Thumbnail alpha salva em: ${thumbnailPath}`);
  }

  for (const [name, url] of Object.entries(task.thumbnail_urls || {})) {
    if (!url) {
      continue;
    }
    const thumbnailPath = path.join(outputDir, `${task.id}-thumbnail-${name}.png`);
    await client.download(url, thumbnailPath);
    console.log(`Thumbnail ${name} salva em: ${thumbnailPath}`);
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

  return options;
}

function parseList(value) {
  if (!value) {
    return [];
  }
  return String(value)
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function getTargetFormats(options) {
  const formats = options.formats || options.format || "glb,3mf";
  return parseList(formats);
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

function printHelp() {
  console.log(`
Uso:
  npm run multi-image-to-3d -- --images "view1.png,view2.png,view3.png,view4.png"

Flags:
  --images <paths-or-urls>          Lista separada por vírgula, 1 a 4 imagens
  --input-task-id <id>              Usa output de uma tarefa API de imagem/multi-view
  --formats <glb,3mf,stl>           Formatos de saída (padrão: glb,3mf)
  --out <dir>                       Pasta de saída
  --ai-model <latest|meshy-7|meshy-6|meshy-5>
  --no-texture                      Pula textura
  --pbr                             Gera mapas PBR
  --texture-resolution <2k|4k|8k>
  --texture-prompt <texto>
  --texture-images <paths-or-urls>  Imagens para guiar textura (1 a 4, meshy-7/latest)
  --remesh / --no-remesh
  --target-polycount <numero>
  --topology <triangle|quad>
  --decimation-mode <1|2|3|4>
  --save-pre-remeshed-model
  --pose-mode <a-pose|t-pose>
  --no-image-enhancement
  --no-remove-lighting
  --auto-size
  --alpha-thumbnail
  --multi-view-thumbnails
`);
}
