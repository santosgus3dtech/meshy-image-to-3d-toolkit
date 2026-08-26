import argparse
from html import escape
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import trimesh
from PIL import Image, ImageFilter
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from trimesh.visual.color import uv_to_color


DEFAULT_PALETTE = {
    "marrom": {
        "display": [112, 70, 44, 255],
        "anchors": [
            [64, 32, 0],
            [80, 45, 20],
            [96, 64, 32],
            [128, 82, 52],
            [160, 96, 64],
            [32, 0, 0],
            [64, 32, 32],
        ],
    },
    "bege": {
        "display": [235, 205, 145, 255],
        "anchors": [
            [224, 192, 128],
            [224, 192, 160],
            [245, 225, 170],
            [224, 160, 128],
            [224, 160, 96],
            [192, 160, 96],
            [160, 128, 96],
        ],
    },
    "laranja_amarelo": {
        "display": [245, 184, 38, 255],
        "anchors": [
            [224, 160, 0],
            [224, 160, 32],
            [224, 192, 32],
            [255, 203, 42],
            [240, 185, 30],
        ],
    },
    "preto": {
        "display": [20, 18, 18, 255],
        "anchors": [
            [0, 0, 0],
            [18, 18, 20],
            [32, 32, 32],
            [45, 42, 40],
            [32, 32, 0],
        ],
    },
}


def main():
    args = parse_args()
    input_path = Path(args.input).resolve()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    scene = trimesh.load(input_path, force="scene")
    if len(scene.geometry) != 1:
        print(f"Aviso: arquivo tem {len(scene.geometry)} geometrias; elas serão concatenadas.")
        mesh = scene.dump(concatenate=True)
    else:
        mesh = next(iter(scene.geometry.values()))

    texture = get_base_color_texture(mesh)
    if texture is None:
        raise SystemExit("O GLB não tem baseColorTexture. Não dá para separar por cor da textura.")
    if mesh.visual.uv is None:
        raise SystemExit("O GLB não tem UVs. Não dá para mapear faces para cores da textura.")

    texture = texture.convert("RGBA")
    sampling_texture = clean_texture_for_sampling(texture, args.texture_median_size)
    face_colors = sample_face_colors(mesh, sampling_texture)
    labels, label_names, distances = classify_colors(face_colors[:, :3], DEFAULT_PALETTE)
    min_faces_by_label = parse_min_faces(args.min_island_faces, args.min_island_faces_by_color, label_names)
    drop_faces_by_label = parse_min_faces(
        args.drop_output_components_below_faces,
        args.drop_output_components_by_color,
        label_names,
    )
    labels, cleanup_stats = cleanup_small_islands(
        mesh=mesh,
        labels=labels,
        label_names=label_names,
        min_faces_by_label=min_faces_by_label,
        passes=args.cleanup_passes,
    )

    manifest = {
        "input": str(input_path),
        "texture_size": list(texture.size),
        "texture_median_size": args.texture_median_size,
        "solidify_thickness": args.solidify_thickness,
        "solidify_direction": args.solidify_direction,
        "vertices": int(len(mesh.vertices)),
        "faces": int(len(mesh.faces)),
        "cleanup": {
            "passes": args.cleanup_passes,
            "min_faces_by_color": {
                label_names[index]: int(min_faces_by_label[index]) for index in range(len(label_names))
            },
            "stats": cleanup_stats,
        },
        "drop_output_components_below_faces": {
            label_names[index]: int(drop_faces_by_label[index]) for index in range(len(label_names))
        },
        "palette": {
            name: {
                "display": DEFAULT_PALETTE[name]["display"],
                "anchors": DEFAULT_PALETTE[name]["anchors"],
            }
            for name in label_names
        },
        "parts": [],
        "notes": [
            "Separacao baseada na cor amostrada da textura no centro de cada face.",
            "Os arquivos STL por cor podem conter bordas abertas nas transicoes entre cores.",
            "Para impressao multicolor em slicer, use o GLB preview ou importe os STLs como partes alinhadas.",
        ],
    }

    preview_scene = trimesh.Scene()
    base_name = input_path.stem
    full_color_mesh = build_full_color_mesh(mesh, labels, label_names, DEFAULT_PALETTE)
    full_color_glb_path = output_dir / f"{base_name}-colored-full.glb"
    full_color_mesh.export(full_color_glb_path)
    manifest["colored_full_glb"] = str(full_color_glb_path)

    if args.export_colored_3mf:
        full_color_3mf_path = output_dir / f"{base_name}-colored-full.3mf"
        write_colored_3mf(full_color_mesh, labels, label_names, DEFAULT_PALETTE, full_color_3mf_path)
        manifest["colored_full_3mf"] = str(full_color_3mf_path)
        print(f"Modelo inteiro colorido 3MF -> {full_color_3mf_path}")

    print(f"Modelo inteiro colorido GLB -> {full_color_glb_path}")

    for label_index, name in enumerate(label_names):
        mask = labels == label_index
        face_count = int(mask.sum())
        if face_count == 0:
            continue

        part_mesh = trimesh.Trimesh(
            vertices=mesh.vertices.copy(),
            faces=mesh.faces[mask].copy(),
            process=False,
        )
        part_mesh.remove_unreferenced_vertices()
        part_mesh, dropped_components = drop_small_output_components(
            part_mesh,
            min_faces=int(drop_faces_by_label[label_index]),
        )
        before_solidify_faces = int(len(part_mesh.faces))

        if args.solidify_thickness > 0:
            part_mesh = solidify_surface_mesh(
                part_mesh,
                thickness=float(args.solidify_thickness),
                direction=args.solidify_direction,
            )

        color = np.array(DEFAULT_PALETTE[name]["display"], dtype=np.uint8)
        part_mesh.visual = trimesh.visual.ColorVisuals(
            part_mesh,
            face_colors=np.tile(color, (len(part_mesh.faces), 1)),
        )

        stl_path = output_dir / f"{base_name}-{name}.stl"
        glb_path = output_dir / f"{base_name}-{name}.glb"
        part_mesh.export(stl_path)
        part_mesh.export(glb_path)
        preview_scene.add_geometry(part_mesh, node_name=name, geom_name=name)

        part = {
            "name": name,
            "faces": face_count,
            "share": round(face_count / len(mesh.faces), 6),
            "vertices": int(len(part_mesh.vertices)),
            "faces_before_solidify": before_solidify_faces,
            "exported_faces": int(len(part_mesh.faces)),
            "watertight": bool(part_mesh.is_watertight),
            "dropped_components": dropped_components,
            "stl": str(stl_path),
            "glb": str(glb_path),
            "mean_distance_lab": round(float(distances[mask].mean()), 3),
        }
        manifest["parts"].append(part)
        drop_text = ""
        if dropped_components["faces"]:
            drop_text = (
                f" ({dropped_components['faces']:,} faces soltas descartadas "
                f"em {dropped_components['components']} componentes)"
            )
        watertight_text = " fechado" if part_mesh.is_watertight else " aberto"
        print(f"{name}: {len(part_mesh.faces):,} faces exportadas{watertight_text}{drop_text} -> {stl_path.name}")

    preview_path = output_dir / f"{base_name}-segmentado-preview.glb"
    preview_scene.export(preview_path)
    manifest["preview_glb"] = str(preview_path)

    texture_path = output_dir / f"{base_name}-texture.png"
    texture.save(texture_path)
    manifest["texture"] = str(texture_path)

    texture_mask_path = output_dir / f"{base_name}-texture-mask.png"
    save_texture_mask(sampling_texture, DEFAULT_PALETTE, texture_mask_path)
    manifest["texture_mask"] = str(texture_mask_path)

    if sampling_texture is not texture:
        sampling_texture_path = output_dir / f"{base_name}-texture-cleaned-for-sampling.png"
        sampling_texture.save(sampling_texture_path)
        manifest["sampling_texture"] = str(sampling_texture_path)

    manifest_path = output_dir / f"{base_name}-split-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Preview segmentado -> {preview_path}")
    print(f"Manifest -> {manifest_path}")


def parse_args():
    parser = argparse.ArgumentParser(description="Divide um GLB texturizado em arquivos por grupos de cor.")
    parser.add_argument("--input", required=True, help="Caminho do GLB de entrada.")
    parser.add_argument("--output", default="outputs/split_by_color", help="Pasta de saída.")
    parser.add_argument(
        "--texture-median-size",
        type=int,
        default=5,
        help="Filtro de mediana aplicado na textura antes da classificacao. Use 1 para desligar.",
    )
    parser.add_argument(
        "--min-island-faces",
        type=int,
        default=500,
        help="Ilhas menores que esse numero de faces sao absorvidas pela cor vizinha dominante.",
    )
    parser.add_argument(
        "--min-island-faces-by-color",
        default="preto=2500,laranja_amarelo=150",
        help="Overrides por cor, exemplo: preto=4000,laranja_amarelo=100.",
    )
    parser.add_argument(
        "--cleanup-passes",
        type=int,
        default=2,
        help="Quantidade de passadas de limpeza de ilhas pequenas.",
    )
    parser.add_argument(
        "--drop-output-components-below-faces",
        type=int,
        default=0,
        help="Descarta componentes isolados menores que esse numero de faces no arquivo exportado.",
    )
    parser.add_argument(
        "--drop-output-components-by-color",
        default="preto=1000",
        help="Overrides por cor para descarte final, exemplo: preto=2000,marrom=100.",
    )
    parser.add_argument(
        "--solidify-thickness",
        type=float,
        default=0.0,
        help="Adiciona espessura aos STLs por cor. Use algo como 0.015 neste modelo.",
    )
    parser.add_argument(
        "--solidify-direction",
        choices=["inward", "outward", "both"],
        default="inward",
        help="Direcao da espessura em relacao a superficie original.",
    )
    parser.add_argument(
        "--export-colored-3mf",
        action="store_true",
        help="Exporta um 3MF unico com cores por face, melhor para slicer multicolor.",
    )
    return parser.parse_args()


def get_base_color_texture(mesh):
    material = getattr(mesh.visual, "material", None)
    if material is None:
        return None
    return getattr(material, "baseColorTexture", None) or getattr(material, "image", None)


def sample_face_colors(mesh, texture: Image.Image):
    face_uv = mesh.visual.uv[mesh.faces].mean(axis=1)
    return uv_to_color(face_uv, texture)


def clean_texture_for_sampling(texture, median_size):
    if median_size <= 1:
        return texture
    if median_size % 2 == 0:
        median_size += 1
    return texture.filter(ImageFilter.MedianFilter(size=median_size))


def parse_min_faces(default_min_faces, overrides_text, label_names):
    min_faces = np.full(len(label_names), max(0, int(default_min_faces)), dtype=np.int32)
    name_to_index = {name: index for index, name in enumerate(label_names)}

    if not overrides_text:
        return min_faces

    for item in overrides_text.split(","):
        if not item.strip():
            continue
        if "=" not in item:
            raise SystemExit(f"Override inválido em --min-island-faces-by-color: {item}")
        name, value = [part.strip() for part in item.split("=", 1)]
        if name not in name_to_index:
            raise SystemExit(f"Cor desconhecida em --min-island-faces-by-color: {name}")
        min_faces[name_to_index[name]] = max(0, int(value))

    return min_faces


def cleanup_small_islands(mesh, labels, label_names, min_faces_by_label, passes):
    if passes <= 0 or int(min_faces_by_label.max()) <= 1:
        return labels, []

    adjacency = mesh.face_adjacency
    cleaned = labels.copy()
    stats = []

    for pass_index in range(passes):
        source = cleaned.copy()
        next_labels = source.copy()
        pass_stats = []

        for label_index, name in enumerate(label_names):
            min_faces = int(min_faces_by_label[label_index])
            if min_faces <= 1:
                continue

            changes = find_small_island_reassignments(source, adjacency, label_index, min_faces, len(label_names))
            if not changes:
                continue

            moved_faces = 0
            moved_components = 0
            target_counts = {}
            for component_faces, target_label in changes:
                next_labels[component_faces] = target_label
                moved_faces += len(component_faces)
                moved_components += 1
                target_name = label_names[target_label]
                target_counts[target_name] = target_counts.get(target_name, 0) + len(component_faces)

            pass_stats.append(
                {
                    "from": name,
                    "components": moved_components,
                    "faces": moved_faces,
                    "targets": target_counts,
                }
            )

        cleaned = next_labels
        stats.append({"pass": pass_index + 1, "changes": pass_stats})
        moved_in_pass = sum(change["faces"] for change in pass_stats)
        print(f"Limpeza pass {pass_index + 1}: {moved_in_pass:,} faces reatribuídas")
        if moved_in_pass == 0:
            break

    return cleaned, stats


def drop_small_output_components(mesh, min_faces):
    if min_faces <= 1 or len(mesh.faces) == 0:
        return mesh, {"components": 0, "faces": 0}

    components = mesh.split(only_watertight=False)
    if len(components) <= 1:
        return mesh, {"components": 0, "faces": 0}

    keep = [component for component in components if len(component.faces) >= min_faces]
    drop = [component for component in components if len(component.faces) < min_faces]

    if not keep:
        largest = max(components, key=lambda component: len(component.faces))
        drop = [component for component in components if component is not largest]
        keep = [largest]

    dropped_faces = int(sum(len(component.faces) for component in drop))
    if dropped_faces == 0:
        return mesh, {"components": 0, "faces": 0}

    return trimesh.util.concatenate(keep), {
        "components": len(drop),
        "faces": dropped_faces,
        "threshold_faces": int(min_faces),
    }


def build_full_color_mesh(mesh, labels, label_names, palette):
    colors = np.array([palette[name]["display"] for name in label_names], dtype=np.uint8)
    colored_mesh = trimesh.Trimesh(
        vertices=mesh.vertices.copy(),
        faces=mesh.faces.copy(),
        process=False,
    )
    colored_mesh.visual = trimesh.visual.ColorVisuals(
        colored_mesh,
        face_colors=colors[labels],
    )
    return colored_mesh


def solidify_surface_mesh(mesh, thickness, direction):
    if thickness <= 0 or len(mesh.faces) == 0:
        return mesh

    vertices = mesh.vertices.copy()
    faces = mesh.faces.copy()
    normals = mesh.vertex_normals.copy()
    norm = np.linalg.norm(normals, axis=1)
    missing = norm < 1e-12
    if np.any(missing):
        normals[missing] = [0.0, 0.0, 1.0]
        norm[missing] = 1.0
    normals = normals / norm[:, None]

    if direction == "outward":
        outer_vertices = vertices + normals * thickness
        inner_vertices = vertices
    elif direction == "both":
        outer_vertices = vertices + normals * (thickness / 2.0)
        inner_vertices = vertices - normals * (thickness / 2.0)
    else:
        outer_vertices = vertices
        inner_vertices = vertices - normals * thickness

    vertex_count = len(vertices)
    solid_vertices = np.vstack([outer_vertices, inner_vertices])
    outer_faces = faces
    inner_faces = faces[:, ::-1] + vertex_count

    boundary_edges = find_boundary_edges(faces)
    if len(boundary_edges):
        a = boundary_edges[:, 0]
        b = boundary_edges[:, 1]
        side_faces = np.vstack(
            [
                np.column_stack([a, b, b + vertex_count]),
                np.column_stack([a, b + vertex_count, a + vertex_count]),
            ]
        )
        solid_faces = np.vstack([outer_faces, inner_faces, side_faces])
    else:
        solid_faces = np.vstack([outer_faces, inner_faces])

    solid = trimesh.Trimesh(vertices=solid_vertices, faces=solid_faces, process=False)
    solid.update_faces(solid.unique_faces())
    solid.update_faces(solid.nondegenerate_faces())
    solid.remove_unreferenced_vertices()
    trimesh.repair.fix_normals(solid, multibody=True)
    return solid


def find_boundary_edges(faces):
    edges = np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    sorted_edges = np.sort(edges, axis=1)
    _, inverse, counts = np.unique(sorted_edges, axis=0, return_inverse=True, return_counts=True)
    return edges[counts[inverse] == 1]


def write_colored_3mf(mesh, labels, label_names, palette, output_path):
    output_path = Path(output_path)
    colors = [palette[name]["display"] for name in label_names]

    with ZipFile(output_path, "w", compression=ZIP_DEFLATED, compresslevel=6) as package:
        package.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
            '  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
            "</Types>\n",
        )
        package.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            '  <Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
            "</Relationships>\n",
        )

        with package.open("3D/3dmodel.model", "w") as file:
            write_xml_line(file, '<?xml version="1.0" encoding="UTF-8"?>')
            write_xml_line(
                file,
                '<model unit="millimeter" xml:lang="en-US" '
                'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">',
            )
            write_xml_line(file, '  <metadata name="Application">api_meshyai split_by_color.py</metadata>')
            write_xml_line(file, "  <resources>")
            write_xml_line(file, '    <basematerials id="1">')
            for name, color in zip(label_names, colors):
                display = f"#{color[0]:02X}{color[1]:02X}{color[2]:02X}{color[3]:02X}"
                write_xml_line(file, f'      <base name="{escape(name)}" displaycolor="{display}"/>')
            write_xml_line(file, "    </basematerials>")
            write_xml_line(file, '    <object id="2" type="model">')
            write_xml_line(file, "      <mesh>")
            write_xml_line(file, "        <vertices>")
            for vertex in mesh.vertices:
                write_xml_line(
                    file,
                    f'          <vertex x="{vertex[0]:.9g}" y="{vertex[1]:.9g}" z="{vertex[2]:.9g}"/>',
                )
            write_xml_line(file, "        </vertices>")
            write_xml_line(file, "        <triangles>")
            for face, label in zip(mesh.faces, labels):
                label = int(label)
                write_xml_line(
                    file,
                    f'          <triangle v1="{face[0]}" v2="{face[1]}" v3="{face[2]}" '
                    f'pid="1" p1="{label}" p2="{label}" p3="{label}"/>',
                )
            write_xml_line(file, "        </triangles>")
            write_xml_line(file, "      </mesh>")
            write_xml_line(file, "    </object>")
            write_xml_line(file, "  </resources>")
            write_xml_line(file, "  <build>")
            write_xml_line(file, '    <item objectid="2"/>')
            write_xml_line(file, "  </build>")
            write_xml_line(file, "</model>")


def write_xml_line(file, text):
    file.write((text + "\n").encode("utf-8"))


def find_small_island_reassignments(labels, adjacency, label_index, min_faces, label_count):
    face_indexes = np.flatnonzero(labels == label_index)
    if len(face_indexes) == 0:
        return []

    local_index = np.full(len(labels), -1, dtype=np.int32)
    local_index[face_indexes] = np.arange(len(face_indexes), dtype=np.int32)

    same_label_edges = (labels[adjacency[:, 0]] == label_index) & (labels[adjacency[:, 1]] == label_index)
    same_edges = adjacency[same_label_edges]
    rows = local_index[same_edges[:, 0]]
    cols = local_index[same_edges[:, 1]]
    graph = coo_matrix(
        (
            np.ones(len(rows) * 2, dtype=np.uint8),
            (np.r_[rows, cols], np.r_[cols, rows]),
        ),
        shape=(len(face_indexes), len(face_indexes)),
    ).tocsr()

    component_count, local_components = connected_components(graph, directed=False)
    component_sizes = np.bincount(local_components, minlength=component_count)
    small_components = np.flatnonzero(component_sizes < min_faces)
    if len(small_components) == 0:
        return []

    global_component = np.full(len(labels), -1, dtype=np.int32)
    global_component[face_indexes] = local_components

    left = adjacency[:, 0]
    right = adjacency[:, 1]
    left_is_label = labels[left] == label_index
    right_is_label = labels[right] == label_index
    boundary = left_is_label ^ right_is_label
    if not np.any(boundary):
        return []

    label_faces = np.where(left_is_label[boundary], left[boundary], right[boundary])
    neighbor_faces = np.where(left_is_label[boundary], right[boundary], left[boundary])
    boundary_components = global_component[label_faces]
    neighbor_labels = labels[neighbor_faces]

    is_small_boundary = np.isin(boundary_components, small_components)
    if not np.any(is_small_boundary):
        return []

    neighbor_votes = np.zeros((component_count, label_count), dtype=np.int32)
    np.add.at(
        neighbor_votes,
        (boundary_components[is_small_boundary], neighbor_labels[is_small_boundary]),
        1,
    )

    changes = []
    for component_id in small_components:
        votes = neighbor_votes[component_id].copy()
        votes[label_index] = 0
        if votes.max() == 0:
            continue
        target_label = int(votes.argmax())
        component_faces = face_indexes[local_components == component_id]
        changes.append((component_faces, target_label))

    return changes


def classify_colors(rgb, palette):
    names = list(palette.keys())
    anchor_labels = []
    anchors = []

    for label_index, name in enumerate(names):
        for color in palette[name]["anchors"]:
            anchors.append(color)
            anchor_labels.append(label_index)

    lab = srgb_to_lab(rgb.astype(np.float64))
    anchor_lab = srgb_to_lab(np.array(anchors, dtype=np.float64))
    delta = lab[:, None, :] - anchor_lab[None, :, :]
    distances_to_anchors = np.linalg.norm(delta, axis=2)
    nearest_anchor = np.argmin(distances_to_anchors, axis=1)
    labels = np.array(anchor_labels, dtype=np.int16)[nearest_anchor]
    distances = distances_to_anchors[np.arange(len(rgb)), nearest_anchor]
    return labels, names, distances


def save_texture_mask(texture, palette, output_path):
    rgba = np.asarray(texture.convert("RGBA"))
    flat_rgb = rgba[:, :, :3].reshape(-1, 3)
    labels, names, _ = classify_colors(flat_rgb, palette)
    display_colors = np.array([palette[name]["display"] for name in names], dtype=np.uint8)
    mask = display_colors[labels].reshape(rgba.shape)
    mask[:, :, 3] = rgba[:, :, 3]
    Image.fromarray(mask, mode="RGBA").save(output_path)


def srgb_to_lab(rgb):
    srgb = rgb / 255.0
    linear = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)

    matrix = np.array(
        [
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ]
    )
    xyz = linear @ matrix.T
    white = np.array([0.95047, 1.00000, 1.08883])
    xyz_scaled = xyz / white

    epsilon = 216 / 24389
    kappa = 24389 / 27
    f = np.where(xyz_scaled > epsilon, np.cbrt(xyz_scaled), (kappa * xyz_scaled + 16) / 116)

    lab = np.empty_like(f)
    lab[:, 0] = 116 * f[:, 1] - 16
    lab[:, 1] = 500 * (f[:, 0] - f[:, 1])
    lab[:, 2] = 200 * (f[:, 1] - f[:, 2])
    return lab


if __name__ == "__main__":
    main()
