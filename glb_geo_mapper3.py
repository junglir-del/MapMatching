import trimesh
import numpy as np
import json
import csv
import osmium
import time
from tqdm import tqdm

CITY_BBOX = {
    "New York Manhattan": (40.70, -74.02, 40.88, -73.90),
    "London Camden": (51.52, -0.19, 51.56, -0.11),
    "London Canary Wharf": (51.49, -0.02, 51.51, 0.02),
    "Shanghai Pudong": (31.20, 121.45, 31.27, 121.55)
}

# =========================
# 对齐配置类
# =========================
class AlignmentConfig:
    def __init__(self, rotation_degrees=0, scale_factor=1.0, 
                 offset_x=0.0, offset_y=0.0):
        self.rotation_rad = np.radians(rotation_degrees)
        self.scale_factor = scale_factor
        self.offset_x = offset_x
        self.offset_y = offset_y
    
    def apply(self, points):
        cos_theta = np.cos(self.rotation_rad)
        sin_theta = np.sin(self.rotation_rad)
        rotation_matrix = np.array([
            [cos_theta, -sin_theta],
            [sin_theta, cos_theta]
        ])
        
        rotated = points @ rotation_matrix.T * self.scale_factor
        translated = rotated + np.array([self.offset_x, self.offset_y])
        
        return translated

# =========================
# 加载 GLB
# =========================
def load_glb(path):
    print(f"\n📦 正在加载 GLB 模型: {path}")
    print("   (这可能需要 10-30 秒)\n")
    
    start_time = time.time()
    scene = trimesh.load(path, force='scene')
    buildings = []
    centers = []
    
    total_geoms = len(scene.geometry)
    
    for name, geom in tqdm(scene.geometry.items(), desc="   提取建筑中心", 
                           total=total_geoms, unit="个"):
        try:
            c = np.array(geom.centroid)
            ground_center = np.array([c[0], c[2]])
            buildings.append({"id": name, "center": ground_center})
            centers.append(ground_center)
        except:
            continue

    if not centers:
        raise Exception("模型中未提取到有效几何体中心")

    centers = np.array(centers)
    elapsed = time.time() - start_time
    print(f"   ✓ 成功加载 {len(buildings)} 个建筑 (耗时 {elapsed:.1f}秒)\n")
    
    return buildings, centers

# =========================
# OSM 解析
# =========================
class BuildingHandler(osmium.SimpleHandler):
    def __init__(self, bbox, pbar):
        super().__init__()
        self.minlat, self.minlon, self.maxlat, self.maxlon = bbox
        self.buildings = []
        self.count = 0
        self.pbar = pbar
    
    def node(self, n):
        if 'building' in n.tags:
            lat, lon = n.location.lat, n.location.lon
            
            if self.minlat <= lat <= self.maxlat and self.minlon <= lon <= self.maxlon:
                norm_x = (lon - self.minlon) / (self.maxlon - self.minlon + 1e-9)
                norm_y = (lat - self.minlat) / (self.maxlat - self.minlat + 1e-9)
                
                self.buildings.append({
                    "id": f"node_{n.id}",
                    "osm_id": n.id,
                    "osm_type": "node",
                    "center": np.array([norm_x, norm_y]),
                    "tags": dict(n.tags)
                })
                self.count += 1
                self.pbar.update(1)
    
    def way(self, w):
        if 'building' in w.tags:
            try:
                lats = []
                lons = []
                for node in w.nodes:
                    lats.append(node.lat)
                    lons.append(node.lon)
                
                if lats and lons:
                    lat = np.mean(lats)
                    lon = np.mean(lons)
                    
                    if self.minlat <= lat <= self.maxlat and self.minlon <= lon <= self.maxlon:
                        norm_x = (lon - self.minlon) / (self.maxlon - self.minlon + 1e-9)
                        norm_y = (lat - self.minlat) / (self.maxlat - self.minlat + 1e-9)
                        
                        self.buildings.append({
                            "id": f"way_{w.id}",
                            "osm_id": w.id,
                            "osm_type": "way",
                            "center": np.array([norm_x, norm_y]),
                            "tags": dict(w.tags)
                        })
                        self.count += 1
                        self.pbar.update(1)
            except:
                pass

def fetch_osm_from_pbf(pbf_file, city, district):
    key = f"{city} {district}"
    if key not in CITY_BBOX:
        raise Exception(f"未定义区域bbox: {key}")

    bbox = CITY_BBOX[key]
    minlat, minlon, maxlat, maxlon = bbox
    
    print(f"\n🗺️  正在解析 OSM 建筑数据")
    print(f"   文件: {pbf_file}")
    print(f"   区域: {city} {district}")
    print(f"   坐标范围: ({minlat}, {minlon}, {maxlat}, {maxlon})")
    print("   (这可能需要 1-5 分钟)\n")
    
    try:
        start_time = time.time()
        
        pbar = tqdm(
            desc="   扫描 PBF 文件",
            unit=" 建筑",
            unit_scale=False,
            bar_format='{desc}: {n} 个 [{elapsed}<{remaining}, {rate_fmt}]'
        )
        
        try:
            handler = BuildingHandler(bbox, pbar)
            handler.apply_file(pbf_file)
        finally:
            pbar.close()
        
        buildings = handler.buildings
        
        if not buildings:
            raise Exception(f"未在指定区域找到建筑数据")
        
        elapsed = time.time() - start_time
        print(f"\n   ✓ 成功解析 {len(buildings)} 个 OSM 建筑 (耗时 {elapsed:.1f}秒)\n")
        return buildings
        
    except FileNotFoundError:
        raise Exception(f"OSM 文件不存在: {pbf_file}")
    except Exception as e:
        raise Exception(f"解析 OSM 文件失败: {e}")

# =========================
# 匹配引擎
# =========================
def match_engine(glb_buildings, osm_buildings, alignment_config, distance_threshold=0.15):
    glb_centers = np.array([b["center"] for b in glb_buildings])
    g_min, g_max = glb_centers.min(axis=0), glb_centers.max(axis=0)
    
    aligned_centers = alignment_config.apply(glb_centers)
    norm_aligned = (aligned_centers - aligned_centers.min(axis=0)) / (
        aligned_centers.max(axis=0) - aligned_centers.min(axis=0) + 1e-9
    )
    
    results = []
    matched_count = 0
    
    for idx, g in enumerate(glb_buildings):
        norm_g = norm_aligned[idx]
        
        best_match = None
        min_dist = float('inf')

        for o in osm_buildings:
            dist = np.linalg.norm(norm_g - o["center"])
            if dist < min_dist:
                min_dist = dist
                best_match = o
        
        if min_dist < distance_threshold and best_match:
            results.append({
                "glb_id": g["id"],
                "osm": best_match,
                "distance": float(min_dist)
            })
            matched_count += 1
    
    match_rate = (matched_count / len(glb_buildings) * 100) if glb_buildings else 0
    return results, match_rate, matched_count

# =========================
# 自动检测最优旋转角度（两阶段搜索）
# =========================
def auto_detect_rotation(glb_buildings, osm_buildings, coarse_step=15, fine_step=1):
    """
    两阶段搜索最优旋转角度：
    1. 粗搜索：每 coarse_step 度尝试一次
    2. 细搜索：在最优附近，每 fine_step 度尝试一次
    """
    print("\n🔄 自动检测最优旋转角度...\n")
    print("=" * 60)
    
    # 第一阶段：粗搜索
    print(f"📍 第一阶段：粗搜索（步长 {coarse_step}°）\n")
    best_rotation = 0
    best_match_rate = 0
    best_matched_count = 0
    
    search_angles = list(range(0, 360, coarse_step))
    
    for rotation in tqdm(search_angles, desc="   粗搜索进度", unit="°"):
        config = AlignmentConfig(rotation_degrees=rotation)
        matches, match_rate, matched_count = match_engine(
            glb_buildings, osm_buildings, config
        )
        
        if match_rate > best_match_rate:
            best_match_rate = match_rate
            best_rotation = rotation
            best_matched_count = matched_count
    
    print(f"\n   粗搜索最优: {best_rotation}° (匹配率 {best_match_rate:.1f}%)\n")
    
    # 第二阶段：细搜索（在最优值 ±coarse_step 范围内）
    print(f"📍 第二阶段：细搜索（步长 {fine_step}°）\n")
    search_range = np.arange(
        best_rotation - coarse_step, 
        best_rotation + coarse_step + 1, 
        fine_step
    )
    
    for rotation in tqdm(search_range, desc="   细搜索进度", unit="°"):
        rotation = round(rotation, 2)
        config = AlignmentConfig(rotation_degrees=rotation)
        matches, match_rate, matched_count = match_engine(
            glb_buildings, osm_buildings, config
        )
        
        if match_rate > best_match_rate:
            best_match_rate = match_rate
            best_rotation = rotation
            best_matched_count = matched_count
    
    print("\n" + "=" * 60)
    print(f"\n✓ 最优角度: {best_rotation}° (匹配率 {best_match_rate:.1f}%, {best_matched_count} 个建筑匹配)\n")
    
    return best_rotation, best_match_rate, best_matched_count

# =========================
# 材料推断
# =========================
def infer_material(tags):
    if not tags:
        return "unknown"
    
    material = tags.get("building:material", "").lower()
    if material:
        return material
    
    btype = tags.get("building", "").lower()
    if "office" in btype or "commercial" in btype:
        return "glass curtain wall"
    if "industrial" in btype or "warehouse" in btype:
        return "metal/steel"
    if "residential" in btype or "apartment" in btype or "house" in btype:
        return "brick/concrete"
    
    return "concrete"

# =========================
# 导出结果
# =========================
def export_results(matches, alignment_config, match_rate, matched_count):
    """导出为 CSV 和 JSON"""
    csv_rows = []
    json_data = {}

    for r in matches:
        o = r.get("osm")
        if not o:
            continue

        glb_id = r["glb_id"]
        tags = o.get("tags", {})
        name = tags.get("name", "unnamed")
        btype = tags.get("building", "unknown")
        material = infer_material(tags)

        json_data[str(glb_id)] = {
            "glb_id": glb_id,
            "osm_id": o.get("osm_id"),
            "osm_type": o.get("osm_type"),
            "name": name,
            "building_type": btype,
            "material": material,
            "match_distance": round(r["distance"], 4),
            "osm_tags": tags
        }
        csv_rows.append([
            glb_id,
            o.get("osm_id"),
            o.get("osm_type"),
            name,
            btype,
            material
        ])

    # 写 CSV
    with open("result.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["GLB_ID", "OSM_ID", "OSM_TYPE", "NAME", "BUILDING_TYPE", "MATERIAL"])
        writer.writerows(csv_rows)

    # 写 JSON
    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)

    # 写对齐配置
    config_data = {
        "rotation_degrees": np.degrees(alignment_config.rotation_rad),
        "scale_factor": alignment_config.scale_factor,
        "offset_x": alignment_config.offset_x,
        "offset_y": alignment_config.offset_y,
        "match_rate": match_rate,
        "matched_count": matched_count
    }
    with open("alignment_config.json", "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2, ensure_ascii=False)

    print("✓ 导出完成:")
    print(f"  📄 result.csv ({len(csv_rows)} 条记录)")
    print(f"  📋 result.json (完整 OSM 标签)")
    print(f"  ⚙️  alignment_config.json (对齐参数)")
    print(f"\n✓ 最终匹配率: {match_rate:.1f}%")
    print(f"✓ 已匹配: {matched_count}/{len(json_data)} 个建筑\n")

# =========================
# 主程序
# =========================
def run(glb_path, pbf_file, city, district):
    try:
        print("\n" + "=" * 70)
        print("  GLB-OSM 建筑匹配引擎 (全自动)")
        print("  自动检测最优旋转角度")
        print("=" * 70)
        
        # 1. 加载 GLB
        glb_list, centers = load_glb(glb_path)
        print(f"✓ GLB 加载成功: {len(glb_list)} 个建筑体\n")

        # 2. 解析 OSM
        osm_list = fetch_osm_from_pbf(pbf_file, city, district)

        # 3. 自动检测最优旋转角度
        best_rotation, match_rate, matched_count = auto_detect_rotation(glb_list, osm_list)

        # 4. 用最优角度重新匹配
        print("🔧 用最优参数进行最终匹配...\n")
        alignment_config = AlignmentConfig(rotation_degrees=best_rotation)
        matches, final_match_rate, final_matched_count = match_engine(
            glb_list, osm_list, alignment_config
        )

        # 5. 导出结果
        print("\n💾 正在导出结果...\n")
        export_results(matches, alignment_config, final_match_rate, final_matched_count)
        
        print("=" * 70)
        print("  ✓✓✓ 任务完成！")
        print("=" * 70 + "\n")

    except Exception as e:
        print(f"\n❌ 错误: {e}\n")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 5:
        print("用法: python script.py <model.gltf> <osm.pbf> <City> <District>")
        print("\n示例:")
        print('  python script.py model.gltf london.pbf "London" "Camden"')
        print('  python script.py model.gltf new-york.pbf "New York" "Manhattan"')
        print("\n下载 OSM 数据:")
        print("  https://download.geofabrik.de/")
    else:
        run(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])