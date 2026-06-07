#!/usr/bin/env python3
import argparse
import json
import math
import re
import struct
from pathlib import Path

import numpy as np
import osmium
from mapbox_earcut import triangulate_float64

ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963
FLOAT = 5126
UNSIGNED_INT = 5125
TRIANGLES = 4

# Approximate RF material properties for metadata output.
# Values are frequency/moisture dependent; if a building material is missing or
# unknown, the code deliberately uses brick as the default material and records
# that fallback in the JSON metadata.
MATERIAL_ELECTRICAL_PROPERTIES = {
    "brick": {"dielectric_constant": 4.44, "conductivity_s_per_m": 0.018},
    "bricks": {"dielectric_constant": 4.44, "conductivity_s_per_m": 0.018},
    "concrete": {"dielectric_constant": 5.31, "conductivity_s_per_m": 0.0326},
    "cement": {"dielectric_constant": 5.31, "conductivity_s_per_m": 0.0326},
    "glass": {"dielectric_constant": 6.27, "conductivity_s_per_m": 0.0043},
    "wood": {"dielectric_constant": 1.99, "conductivity_s_per_m": 0.0047},
    "stone": {"dielectric_constant": 5.5, "conductivity_s_per_m": 0.038},
    "plaster": {"dielectric_constant": 2.94, "conductivity_s_per_m": 0.0116},
    "metal": {"dielectric_constant": 1.0, "conductivity_s_per_m": 10000000.0},
    "steel": {"dielectric_constant": 1.0, "conductivity_s_per_m": 10000000.0},
}

SURFACE_ELECTRICAL_PROPERTIES = {
    "asphalt": {"dielectric_constant": 4.5, "conductivity_s_per_m": 0.02},
    "fresh_water": {"dielectric_constant": 80.0, "conductivity_s_per_m": 0.01},
    "grass": {"dielectric_constant": 15.0, "conductivity_s_per_m": 0.03},
    "forest": {"dielectric_constant": 12.0, "conductivity_s_per_m": 0.05},
    "sand": {"dielectric_constant": 3.0, "conductivity_s_per_m": 0.001},
    "soil": {"dielectric_constant": 15.0, "conductivity_s_per_m": 0.02},
}

def parse_meters(value):
    if not value:
        return None
    text = str(value).strip().lower().replace(",", ".")
    match = re.search(r"-?\d+(\.\d+)?", text)
    if not match:
        return None
    return float(match.group(0))


def clean_text(value):
    return str(value).replace("\x00", "").strip()


def building_height(tags):
    height = (
        parse_meters(tags.get("height"))
        or parse_meters(tags.get("building:height"))
        or parse_meters(tags.get("roof:height"))
    )
    if height and height > 0:
        return height

    levels = parse_meters(tags.get("building:levels") or tags.get("levels"))
    if levels and levels > 0:
        return levels * 3.2

    return 8.0


def building_name(tags, osm_id):
    return (
        tags.get("name")
        or tags.get("building:name")
        or tags.get("addr:housename")
        or f"building/{osm_id}"
    )


def feature_name(tags, osm_id, feature_type):
    return clean_text(tags.get("name") or f"{feature_type}/{osm_id}")


def actual_building_material(tags):
    material_keys = [
        "building:material",
        "facade:material",
        "material",
        "wall:material",
        "cladding",
        "roof:material",
    ]

    for key in material_keys:
        value = tags.get(key)
        if value:
            return clean_text(value).lower(), key

    return "unknown", None


def infer_building_material(tags):
    building_type = (tags.get("building") or "").lower()
    amenity = (tags.get("amenity") or "").lower()
    name = (tags.get("name") or "").lower()

    inferred = {
        "apartments": "concrete",
        "residential": "concrete",
        "house": "brick",
        "detached": "brick",
        "terrace": "brick",
        "commercial": "glass",
        "retail": "glass",
        "office": "glass",
        "industrial": "metal",
        "warehouse": "metal",
        "garage": "concrete",
        "school": "brick",
        "university": "brick",
        "hospital": "concrete",
        "hotel": "concrete",
    }

    if building_type in inferred:
        return inferred[building_type], f"building:{building_type}"
    if amenity == "fire_station" or "消防" in name:
        return "brick", "amenity:fire_station"

    return "brick", "default_brick"


def resolve_building_material(tags, material_mode):
    actual, source_key = actual_building_material(tags)

    if actual != "unknown":
        return {
            "osm_material": actual,
            "inferred_material": actual,
            "material_inference_source": f"osm:{source_key}",
        }

    if material_mode == "infer":
        inferred, source = infer_building_material(tags)
        return {
            "osm_material": None,
            "inferred_material": inferred,
            "material_inference_source": source,
        }

    return {
        "osm_material": None,
        "inferred_material": "brick",
        "material_inference_source": "default_brick",
    }


def material_electrical_properties(material):
    lookup_material = (material or "unknown").lower()
    if lookup_material not in MATERIAL_ELECTRICAL_PROPERTIES:
        lookup_material = "brick"

    props = MATERIAL_ELECTRICAL_PROPERTIES[lookup_material]
    return {
        "dielectric_constant": props["dielectric_constant"],
        "conductivity_s_per_m": props["conductivity_s_per_m"],
    }


def infer_surface_material(feature_type, tags):
    if feature_type == "road":
        return "asphalt", f"highway:{tags.get('highway')}"
    if feature_type == "water":
        return "fresh_water", "natural:water"
    if feature_type == "grass":
        return "grass", "surface:grass"
    if feature_type == "forest":
        return "forest", "surface:forest"
    if feature_type == "sand":
        return "sand", "surface:sand"
    return "soil", "default_soil"


def surface_electrical_properties(material):
    lookup_material = (material or "soil").lower()
    if lookup_material not in SURFACE_ELECTRICAL_PROPERTIES:
        lookup_material = "soil"

    props = SURFACE_ELECTRICAL_PROPERTIES[lookup_material]
    return {
        "dielectric_constant": props["dielectric_constant"],
        "conductivity_s_per_m": props["conductivity_s_per_m"],
    }



def material_color(material):
    material = (material or "unknown").lower()
    colors = {
        "brick": [0.62, 0.22, 0.14, 1.0],
        "bricks": [0.62, 0.22, 0.14, 1.0],
        "concrete": [0.58, 0.58, 0.55, 1.0],
        "cement": [0.55, 0.55, 0.52, 1.0],
        "glass": [0.45, 0.72, 0.9, 0.55],
        "steel": [0.48, 0.5, 0.52, 1.0],
        "metal": [0.5, 0.5, 0.5, 1.0],
        "wood": [0.55, 0.34, 0.18, 1.0],
        "stone": [0.5, 0.48, 0.42, 1.0],
        "plaster": [0.78, 0.74, 0.66, 1.0],
        "unknown": [0.72, 0.70, 0.64, 1.0],
    }
    return colors.get(material, colors["unknown"])


def feature_color(feature_type):
    colors = {
        "road": [0.44, 0.46, 0.48, 1.0],
        "water": [0.02, 0.32, 0.90, 1.0],
        "grass": [0.20, 0.62, 0.24, 1.0],
        "forest": [0.05, 0.36, 0.12, 1.0],
        "sand": [0.70, 0.64, 0.44, 1.0],
        "land": [0.52, 0.38, 0.24, 1.0],
    }
    return colors.get(feature_type, colors["land"])


def is_water_feature(tags):
    return (
        tags.get("natural") == "water"
        or tags.get("natural") == "bay"
        or tags.get("water") in ("lake", "pond", "reservoir", "basin")
        or tags.get("landuse") == "reservoir"
        or tags.get("waterway") == "riverbank"
    )


def relation_member_is_way(member):
    member_type = getattr(member, "type", None)
    return member_type == "w" or str(member_type).lower() in ("w", "way")


def join_way_segments(segments):
    remaining = [list(segment) for segment in segments if len(segment) >= 2]
    rings = []

    while remaining:
        ring = remaining.pop(0)
        changed = True

        while changed and ring[0] != ring[-1]:
            changed = False

            for i, segment in enumerate(remaining):
                if ring[-1] == segment[0]:
                    ring.extend(segment[1:])
                elif ring[-1] == segment[-1]:
                    ring.extend(reversed(segment[:-1]))
                elif ring[0] == segment[-1]:
                    ring = segment[:-1] + ring
                elif ring[0] == segment[0]:
                    ring = list(reversed(segment[1:])) + ring
                else:
                    continue

                remaining.pop(i)
                changed = True
                break

        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring)

    return rings


def landcover_type(tags):
    natural = tags.get("natural")
    landuse = tags.get("landuse")
    leisure = tags.get("leisure")

    if natural in ("wood", "scrub") or landuse == "forest":
        return "forest"
    if natural in ("grassland", "heath") or landuse in ("grass", "meadow"):
        return "grass"
    if natural in ("beach", "sand"):
        return "sand"
    if leisure in ("park", "garden", "pitch"):
        return "grass"
    return None


def road_width_m(tags):
    width = parse_meters(tags.get("width"))
    if width and width > 0:
        return width

    highway = tags.get("highway")
    defaults = {
        "motorway": 14.0,
        "trunk": 12.0,
        "primary": 10.0,
        "secondary": 8.0,
        "tertiary": 7.0,
        "residential": 5.5,
        "service": 3.5,
        "footway": 2.0,
        "path": 1.5,
        "cycleway": 2.0,
    }
    return defaults.get(highway, 4.0)


def mercator_xy(lon, lat, origin_lon, origin_lat):
    radius = 6378137.0
    x = math.radians(lon - origin_lon) * radius * math.cos(math.radians(origin_lat))
    y = math.radians(lat - origin_lat) * radius
    return x, y


def point_in_bbox(lon, lat, bbox):
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


def any_point_in_bbox(coords, bbox):
    return any(point_in_bbox(lon, lat, bbox) for lon, lat in coords)


class OsmCollector(osmium.SimpleHandler):
    def __init__(self, bbox):
        super().__init__()
        self.bbox = bbox
        self.buildings = []
        self.roads = []
        self.areas = []
        self.way_geometries = {}

    def way(self, way):
        tags = {tag.k: tag.v for tag in way.tags}

        coords = []
        for node in way.nodes:
            if not node.location.valid():
                return
            coords.append((node.location.lon, node.location.lat))

        if len(coords) < 2:
            return
        if not any_point_in_bbox(coords, self.bbox):
            return

        self.way_geometries[way.id] = coords

        feature = {
            "id": way.id,
            "coords": coords,
            "tags": tags,
        }

        is_closed = len(coords) >= 4 and coords[0] == coords[-1]

        if "building" in tags and is_closed:
            self.buildings.append(feature)
        elif "highway" in tags:
            self.roads.append(feature)
        elif is_closed and is_water_feature(tags):
            feature["feature_type"] = "water"
            self.areas.append(feature)
        elif is_closed:
            cover_type = landcover_type(tags)
            if cover_type:
                feature["feature_type"] = cover_type
                self.areas.append(feature)

    def relation(self, relation):
        tags = {tag.k: tag.v for tag in relation.tags}

        if tags.get("type") != "multipolygon":
            return

        if is_water_feature(tags):
            feature_type = "water"
        else:
            feature_type = landcover_type(tags)

        if not feature_type:
            return

        outer_segments = []
        for member in relation.members:
            if not relation_member_is_way(member):
                continue
            if member.role and member.role != "outer":
                continue

            coords = self.way_geometries.get(member.ref)
            if coords:
                outer_segments.append(coords)

        for ring in join_way_segments(outer_segments):
            if not any_point_in_bbox(ring, self.bbox):
                continue

            self.areas.append({
                "id": relation.id,
                "coords": ring,
                "tags": tags,
                "feature_type": feature_type,
            })


class GltfWriter:
    def __init__(self):
        self.bin = bytearray()
        self.buffer_views = []
        self.accessors = []
        self.meshes = []
        self.nodes = []
        self.materials = []

    def align4(self, pad_byte=0):
        while len(self.bin) % 4:
            self.bin.append(pad_byte)

    def add_bytes(self, data, target):
        self.align4()
        offset = len(self.bin)
        self.bin.extend(data)

        view_index = len(self.buffer_views)
        self.buffer_views.append({
            "buffer": 0,
            "byteOffset": offset,
            "byteLength": len(data),
            "target": target,
        })
        return view_index

    def add_positions(self, positions):
        arr = np.asarray(positions, dtype=np.float32)
        view = self.add_bytes(arr.tobytes(), ARRAY_BUFFER)

        self.accessors.append({
            "bufferView": view,
            "byteOffset": 0,
            "componentType": FLOAT,
            "count": int(len(arr)),
            "type": "VEC3",
            "min": arr.min(axis=0).tolist(),
            "max": arr.max(axis=0).tolist(),
        })
        return len(self.accessors) - 1

    def add_normals(self, normals):
        arr = np.asarray(normals, dtype=np.float32)
        view = self.add_bytes(arr.tobytes(), ARRAY_BUFFER)

        self.accessors.append({
            "bufferView": view,
            "byteOffset": 0,
            "componentType": FLOAT,
            "count": int(len(arr)),
            "type": "VEC3",
        })
        return len(self.accessors) - 1

    def add_indices(self, indices):
        arr = np.asarray(indices, dtype=np.uint32)
        view = self.add_bytes(arr.tobytes(), ELEMENT_ARRAY_BUFFER)

        self.accessors.append({
            "bufferView": view,
            "byteOffset": 0,
            "componentType": UNSIGNED_INT,
            "count": int(len(arr)),
            "type": "SCALAR",
            "min": [int(arr.min())],
            "max": [int(arr.max())],
        })
        return len(self.accessors) - 1

    def add_material(self, name, base_color, metallic=0.0, roughness=0.85):
        material = {
            "name": name,
            "pbrMetallicRoughness": {
                "baseColorFactor": base_color,
                "metallicFactor": metallic,
                "roughnessFactor": roughness,
            },
            "doubleSided": True,
        }

        if base_color[3] < 1.0:
            material["alphaMode"] = "BLEND"

        self.materials.append(material)
        return len(self.materials) - 1

    def add_mesh_node(self, name, positions, normals, indices, material_index, extras):
        pos_accessor = self.add_positions(positions)
        normal_accessor = self.add_normals(normals)
        idx_accessor = self.add_indices(indices)

        mesh_index = len(self.meshes)
        self.meshes.append({
            "name": name,
            "primitives": [{
                "attributes": {
                    "POSITION": pos_accessor,
                    "NORMAL": normal_accessor,
                },
                "indices": idx_accessor,
                "material": material_index,
                "mode": TRIANGLES,
            }],
        })

        self.nodes.append({
            "name": name,
            "mesh": mesh_index,
            "extras": extras,
        })

    def build_gltf(self, buffer_uri=None):
        self.align4()
        buffer = {"byteLength": len(self.bin)}

        if buffer_uri:
            buffer["uri"] = buffer_uri

        return {
            "asset": {
                "version": "2.0",
                "generator": "osm_pbf_to_buildings_glb.py",
            },
            "scene": 0,
            "scenes": [{"nodes": list(range(len(self.nodes)))}],
            "nodes": self.nodes,
            "meshes": self.meshes,
            "materials": self.materials,
            "buffers": [buffer],
            "bufferViews": self.buffer_views,
            "accessors": self.accessors,
        }

    def save(self, output_path):
        output_path = Path(output_path)

        if output_path.suffix.lower() == ".glb":
            self.save_glb(output_path)
        elif output_path.suffix.lower() == ".gltf":
            self.save_gltf(output_path)
        else:
            raise ValueError("Output file must end with .glb or .gltf")

    def save_gltf(self, gltf_path):
        bin_path = gltf_path.with_suffix(".bin")
        gltf = self.build_gltf(buffer_uri=bin_path.name)

        bin_path.write_bytes(bytes(self.bin))
        gltf_path.write_text(
            json.dumps(gltf, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def save_glb(self, glb_path):
        gltf = self.build_gltf(buffer_uri=None)

        json_bytes = json.dumps(
            gltf,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        while len(json_bytes) % 4:
            json_bytes += b" "

        self.align4()
        bin_bytes = bytes(self.bin)

        total_length = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)

        with glb_path.open("wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, total_length))
            f.write(struct.pack("<I4s", len(json_bytes), b"JSON"))
            f.write(json_bytes)
            f.write(struct.pack("<I4s", len(bin_bytes), b"BIN\x00"))
            f.write(bin_bytes)


def triangle_normal(a, b, c):
    ax, ay, az = a
    bx, by, bz = b
    cx, cy, cz = c

    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az

    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx

    length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return nx / length, ny / length, nz / length


def compute_vertex_normals(positions, indices):
    normals = [[0.0, 0.0, 0.0] for _ in positions]

    for i in range(0, len(indices), 3):
        ia, ib, ic = indices[i], indices[i + 1], indices[i + 2]

        n = triangle_normal(
            np.array(positions[ia]),
            np.array(positions[ib]),
            np.array(positions[ic]),
        )

        for idx in (ia, ib, ic):
            normals[idx][0] += n[0]
            normals[idx][1] += n[1]
            normals[idx][2] += n[2]

    for normal in normals:
        length = math.sqrt(sum(v * v for v in normal)) or 1.0
        normal[0] /= length
        normal[1] /= length
        normal[2] /= length

    return normals


def build_building_mesh(coords, height, origin_lon, origin_lat):
    ring = coords[:-1]
    xy = [mercator_xy(lon, lat, origin_lon, origin_lat) for lon, lat in ring]

    if len(xy) < 3:
        return [], [], []

    vertices_2d = np.array([[x, y] for x, y in xy], dtype=np.float64)
    ring_ends = np.array([len(vertices_2d)], dtype=np.uint32)

    top_tris = triangulate_float64(vertices_2d, ring_ends)
    if len(top_tris) < 3:
        return [], [], []

    positions = []

    for x, y in xy:
        positions.append([x, 0.0, -y])

    for x, y in xy:
        positions.append([x, height, -y])

    n = len(xy)
    indices = []

    for i in range(0, len(top_tris), 3):
        a, b, c = [int(v) for v in top_tris[i:i + 3]]

        indices.extend([a + n, b + n, c + n])
        indices.extend([c, b, a])

    for i in range(n):
        j = (i + 1) % n

        indices.extend([i, j, j + n])
        indices.extend([i, j + n, i + n])

    normals = compute_vertex_normals(positions, indices)
    return positions, normals, indices


def build_area_mesh(coords, origin_lon, origin_lat, y=0.01):
    ring = coords[:-1]
    xy = [mercator_xy(lon, lat, origin_lon, origin_lat) for lon, lat in ring]

    if len(xy) < 3:
        return [], [], []

    vertices_2d = np.array([[x, z] for x, z in xy], dtype=np.float64)
    ring_ends = np.array([len(vertices_2d)], dtype=np.uint32)

    tris = triangulate_float64(vertices_2d, ring_ends)
    if len(tris) < 3:
        return [], [], []

    positions = [[x, y, -z] for x, z in xy]
    normals = [[0.0, 1.0, 0.0] for _ in positions]
    indices = [int(v) for v in tris]
    return positions, normals, indices


def bbox_ring(bbox):
    min_lon, min_lat, max_lon, max_lat = bbox
    return [
        (min_lon, min_lat),
        (max_lon, min_lat),
        (max_lon, max_lat),
        (min_lon, max_lat),
        (min_lon, min_lat),
    ]


def build_road_mesh(coords, width, origin_lon, origin_lat, y=0.03):
    xy = [mercator_xy(lon, lat, origin_lon, origin_lat) for lon, lat in coords]

    if len(xy) < 2:
        return [], [], []

    half_width = width / 2.0
    positions = []
    indices = []

    for i, (x, z) in enumerate(xy):
        if i == 0:
            dx = xy[1][0] - x
            dz = xy[1][1] - z
        elif i == len(xy) - 1:
            dx = x - xy[i - 1][0]
            dz = z - xy[i - 1][1]
        else:
            dx = xy[i + 1][0] - xy[i - 1][0]
            dz = xy[i + 1][1] - xy[i - 1][1]

        length = math.sqrt(dx * dx + dz * dz) or 1.0
        nx = -dz / length
        nz = dx / length

        positions.append([x + nx * half_width, y, -(z + nz * half_width)])
        positions.append([x - nx * half_width, y, -(z - nz * half_width)])

    for i in range(len(xy) - 1):
        left_a = i * 2
        right_a = left_a + 1
        left_b = left_a + 2
        right_b = left_a + 3
        indices.extend([left_a, right_a, left_b])
        indices.extend([right_a, right_b, left_b])

    normals = [[0.0, 1.0, 0.0] for _ in positions]
    return positions, normals, indices


def make_building_metadata(building, name, height, material_info):
    tags = building["tags"]
    electrical_props = material_electrical_properties(material_info["inferred_material"])

    return {
        "osm_type": "way",
        "osm_id": building["id"],
        "name": name,
        "building": tags.get("building"),
        "height_m": height,
        "height_tag": tags.get("height"),
        "building_levels": tags.get("building:levels"),
        "osm_material": material_info["osm_material"],
        "inferred_material": material_info["inferred_material"],
        "material_inference_source": material_info["material_inference_source"],
        "dielectric_constant": electrical_props["dielectric_constant"],
        "conductivity_s_per_m": electrical_props["conductivity_s_per_m"],
        "source_tags": tags,
    }


def make_surface_metadata(feature, name, feature_type):
    tags = feature["tags"]
    inferred_material, source = infer_surface_material(feature_type, tags)
    electrical_props = surface_electrical_properties(inferred_material)

    return {
        "osm_type": "way",
        "osm_id": feature["id"],
        "name": name,
        "feature_type": feature_type,
        "inferred_material": inferred_material,
        "material_inference_source": source,
        "dielectric_constant": electrical_props["dielectric_constant"],
        "conductivity_s_per_m": electrical_props["conductivity_s_per_m"],
        "source_tags": tags,
    }


def normalize_output_path(output_arg, output_format):
    output_path = Path(output_arg)

    if output_path.suffix.lower() in (".glb", ".gltf"):
        return output_path

    return output_path.with_suffix("." + output_format)


def main():
    parser = argparse.ArgumentParser(
        description="Convert OSM PBF buildings, roads, and surface areas inside a bbox to glTF/GLB."
    )

    parser.add_argument("pbf", help="Input .osm.pbf file")
    parser.add_argument("output", help="Output file path. Extension can be omitted.")

    parser.add_argument(
        "--format",
        choices=("glb", "gltf"),
        default="glb",
        help="Output 3D format if output has no extension. Default: glb",
    )

    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        required=True,
        metavar=("MIN_LAT", "MIN_LON", "MAX_LAT", "MAX_LON"),
        help="Bounding box format: minLat minLon maxLat maxLon",
    )

    parser.add_argument(
        "--material-mode",
        choices=("actual", "infer"),
        default="actual",
        help="actual: only use OSM material tags; infer: guess material when OSM has no material",
    )

    args = parser.parse_args()

    output_path = normalize_output_path(args.output, args.format)

    min_lat, min_lon, max_lat, max_lon = args.bbox
    min_lon, max_lon = sorted((min_lon, max_lon))
    min_lat, max_lat = sorted((min_lat, max_lat))

    bbox = (min_lon, min_lat, max_lon, max_lat)

    if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180):
        raise ValueError("Longitude must be between -180 and 180.")
    if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        raise ValueError("Latitude must be between -90 and 90.")

    origin_lon = (min_lon + max_lon) / 2
    origin_lat = (min_lat + max_lat) / 2

    print(f"Using bbox input format: minLat minLon maxLat maxLon")
    print(f"Using bbox lon/lat: {min_lon} {min_lat} {max_lon} {max_lat}")
    print(f"Output 3D file: {output_path}")
    print(f"Material mode: {args.material_mode}")

    collector = OsmCollector(bbox)
    collector.apply_file(args.pbf, locations=True)

    writer = GltfWriter()
    material_indices = {}
    exported_building_count = 0
    exported_road_count = 0
    exported_area_count = 0
    extras_items = []

    for feature_type in ("road", "water", "grass", "forest", "sand", "land"):
        material_indices[feature_type] = writer.add_material(
            feature_type,
            feature_color(feature_type),
        )

    ground_feature = {
        "id": "bbox_ground",
        "coords": bbox_ring(bbox),
        "tags": {"generated": "bbox_ground"},
    }
    positions, normals, indices = build_area_mesh(
        ground_feature["coords"],
        origin_lon,
        origin_lat,
        y=-0.02,
    )
    if positions and indices:
        metadata = make_surface_metadata(ground_feature, "bbox_ground", "land")
        metadata["gltf_node_index"] = len(writer.nodes)
        metadata["vertex_count"] = len(positions)
        metadata["triangle_count"] = len(indices) // 3

        writer.add_mesh_node(
            name="bbox_ground",
            positions=positions,
            normals=normals,
            indices=indices,
            material_index=material_indices["land"],
            extras=metadata,
        )
        extras_items.append(metadata)

    for building in collector.buildings:
        tags = building["tags"]

        material_info = resolve_building_material(tags, args.material_mode)
        inferred_material = material_info["inferred_material"]

        if inferred_material not in material_indices:
            material_indices[inferred_material] = writer.add_material(
                f"building_{inferred_material}",
                material_color(inferred_material),
            )

        height = building_height(tags)
        name = clean_text(building_name(tags, building["id"]))

        positions, normals, indices = build_building_mesh(
            building["coords"],
            height,
            origin_lon,
            origin_lat,
        )

        if not positions or not indices:
            continue

        metadata = make_building_metadata(
            building=building,
            name=name,
            height=height,
            material_info=material_info,
        )

        metadata["gltf_node_index"] = len(writer.nodes)
        metadata["vertex_count"] = len(positions)
        metadata["triangle_count"] = len(indices) // 3

        writer.add_mesh_node(
            name=name,
            positions=positions,
            normals=normals,
            indices=indices,
            material_index=material_indices[inferred_material],
            extras=metadata,
        )

        extras_items.append(metadata)
        exported_building_count += 1

    for road in collector.roads:
        tags = road["tags"]
        name = feature_name(tags, road["id"], "road")
        width = road_width_m(tags)

        positions, normals, indices = build_road_mesh(
            road["coords"],
            width,
            origin_lon,
            origin_lat,
        )

        if not positions or not indices:
            continue

        metadata = make_surface_metadata(road, name, "road")
        metadata["highway"] = tags.get("highway")
        metadata["width_m"] = width
        metadata["gltf_node_index"] = len(writer.nodes)
        metadata["vertex_count"] = len(positions)
        metadata["triangle_count"] = len(indices) // 3

        writer.add_mesh_node(
            name=name,
            positions=positions,
            normals=normals,
            indices=indices,
            material_index=material_indices["road"],
            extras=metadata,
        )

        extras_items.append(metadata)
        exported_road_count += 1

    for area in collector.areas:
        feature_type = area["feature_type"]
        name = feature_name(area["tags"], area["id"], feature_type)

        positions, normals, indices = build_area_mesh(
            area["coords"],
            origin_lon,
            origin_lat,
        )

        if not positions or not indices:
            continue

        metadata = make_surface_metadata(area, name, feature_type)
        metadata["gltf_node_index"] = len(writer.nodes)
        metadata["vertex_count"] = len(positions)
        metadata["triangle_count"] = len(indices) // 3

        writer.add_mesh_node(
            name=name,
            positions=positions,
            normals=normals,
            indices=indices,
            material_index=material_indices.get(feature_type, material_indices["land"]),
            extras=metadata,
        )

        extras_items.append(metadata)
        exported_area_count += 1

    writer.save(output_path)

    extras_path = output_path.with_name(output_path.stem + "_extras.json")
    extras_doc = {
        "input_pbf": args.pbf,
        "output_3d": str(output_path),
        "output_format": output_path.suffix.lower().lstrip("."),
        "material_mode": args.material_mode,
        "bbox_format": "minLat minLon maxLat maxLon",
        "bbox_input": args.bbox,
        "bbox_lonlat": {
            "min_lon": min_lon,
            "min_lat": min_lat,
            "max_lon": max_lon,
            "max_lat": max_lat,
        },
        "extras_count": len(extras_items),
        "extras": extras_items,
    }
    extras_path.write_text(
        json.dumps(extras_doc, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Saved 3D file: {output_path}")
    print(f"Saved extras JSON: {extras_path}")
    print(f"Buildings read from OSM: {len(collector.buildings)}")
    print(f"Buildings exported to mesh: {exported_building_count}")
    print(f"Roads read from OSM: {len(collector.roads)}")
    print(f"Roads exported to mesh: {exported_road_count}")
    print(f"Surface areas read from OSM: {len(collector.areas)}")
    print(f"Surface areas exported to mesh: {exported_area_count}")

    if output_path.suffix.lower() == ".gltf":
        print("Note: keep the generated .bin file next to the .gltf file.")


if __name__ == "__main__":
    main()


