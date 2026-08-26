import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage


DEFAULT_CUTS = [0.0, 0.255, 0.495, 0.715, 1.0]
DEFAULT_NAMES = ["front", "front_right", "side", "back"]


def main():
    args = parse_args()
    input_path = Path(args.input).resolve()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    image = Image.open(input_path).convert("RGBA")
    width, height = image.size
    cuts = parse_cuts(args.cuts, width)
    names = parse_names(args.names, len(cuts) - 1)
    min_component_area_by_name = parse_min_component_area(args.min_component_area_by_view)

    outputs = []
    for index, name in enumerate(names):
        x0, x1 = cuts[index], cuts[index + 1]
        crop = image.crop((x0, 0, x1, height))
        prepared, bbox = prepare_view(
            crop,
            args.canvas_size,
            args.max_subject_size,
            args.margin,
            min_component_area_by_name.get(name, 0),
        )
        output_path = output_dir / f"{index + 1:02d}-{name}.png"
        prepared.save(output_path)
        outputs.append(
            {
                "name": name,
                "path": str(output_path),
                "source_crop": [int(x0), 0, int(x1), int(height)],
                "foreground_bbox_in_crop": [int(v) for v in bbox],
            }
        )
        print(f"{name}: {output_path}")

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps({"input": str(input_path), "views": outputs}, indent=2) + "\n", "utf-8")
    print(f"Manifest: {manifest_path}")


def parse_args():
    parser = argparse.ArgumentParser(description="Recorta uma prancha multi-view em imagens separadas.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--cuts",
        default=",".join(str(value) for value in DEFAULT_CUTS),
        help="Cortes horizontais normalizados ou em pixels. Ex: 0,0.255,0.495,0.715,1",
    )
    parser.add_argument("--names", default=",".join(DEFAULT_NAMES))
    parser.add_argument("--canvas-size", type=int, default=1024)
    parser.add_argument("--max-subject-size", type=int, default=900)
    parser.add_argument("--margin", type=int, default=28)
    parser.add_argument(
        "--min-component-area-by-view",
        default="",
        help="Remove componentes pequenos por vista. Ex: front=5000,front_right=5000",
    )
    return parser.parse_args()


def parse_cuts(text, width):
    values = [float(item.strip()) for item in text.split(",") if item.strip()]
    if len(values) < 2:
        raise SystemExit("--cuts precisa de pelo menos dois valores.")
    if max(values) <= 1.0:
        values = [value * width for value in values]
    cuts = [max(0, min(width, round(value))) for value in values]
    if sorted(cuts) != cuts or len(set(cuts)) != len(cuts):
        raise SystemExit("--cuts deve estar em ordem crescente, sem repetição.")
    return cuts


def parse_names(text, count):
    names = [item.strip() for item in text.split(",") if item.strip()]
    if len(names) != count:
        raise SystemExit(f"--names precisa ter {count} nomes.")
    return names


def parse_min_component_area(text):
    result = {}
    if not text:
        return result
    for item in text.split(","):
        if not item.strip():
            continue
        if "=" not in item:
            raise SystemExit(f"Entrada inválida em --min-component-area-by-view: {item}")
        name, value = [part.strip() for part in item.split("=", 1)]
        result[name] = int(value)
    return result


def prepare_view(crop, canvas_size, max_subject_size, margin, min_component_area):
    keyed = remove_green_background(crop)
    if min_component_area > 0:
        keyed = remove_small_alpha_components(keyed, min_component_area)
    alpha = np.asarray(keyed)[:, :, 3]
    rows, cols = np.where(alpha > 10)
    if len(rows) == 0:
        raise SystemExit("Nenhum foreground detectado no recorte.")

    left = max(0, int(cols.min()) - margin)
    right = min(keyed.width, int(cols.max()) + 1 + margin)
    top = max(0, int(rows.min()) - margin)
    bottom = min(keyed.height, int(rows.max()) + 1 + margin)
    subject = keyed.crop((left, top, right, bottom))

    scale = min(max_subject_size / subject.width, max_subject_size / subject.height, 1.0)
    new_size = (max(1, round(subject.width * scale)), max(1, round(subject.height * scale)))
    subject = subject.resize(new_size, Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    x = (canvas_size - subject.width) // 2
    y = (canvas_size - subject.height) // 2
    canvas.alpha_composite(subject, (x, y))
    return canvas, (left, top, right, bottom)


def remove_green_background(image):
    rgba = np.asarray(image.convert("RGBA")).copy()
    rgb = rgba[:, :, :3].astype(np.int16)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]

    green = (g > 95) & (g > r * 1.12 + 20) & (g > b * 1.12 + 20)
    alpha = np.where(green, 0, rgba[:, :, 3]).astype(np.uint8)
    alpha_image = Image.fromarray(alpha, mode="L")
    alpha_image = alpha_image.filter(ImageFilter.MinFilter(size=3))
    alpha_image = alpha_image.filter(ImageFilter.MaxFilter(size=5))
    alpha_image = alpha_image.filter(ImageFilter.GaussianBlur(radius=0.6))

    rgba[:, :, 3] = np.asarray(alpha_image)
    return Image.fromarray(rgba, mode="RGBA")


def remove_small_alpha_components(image, min_area):
    rgba = np.asarray(image.convert("RGBA")).copy()
    alpha_mask = rgba[:, :, 3] > 10
    labels, count = ndimage.label(alpha_mask)
    if count <= 1:
        return image

    component_sizes = np.bincount(labels.ravel())
    keep_labels = np.flatnonzero(component_sizes >= min_area)
    keep_labels = keep_labels[keep_labels != 0]
    keep_mask = np.isin(labels, keep_labels)
    rgba[:, :, 3] = np.where(keep_mask, rgba[:, :, 3], 0).astype(np.uint8)
    return Image.fromarray(rgba, mode="RGBA")


if __name__ == "__main__":
    main()
