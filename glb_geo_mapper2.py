import trimesh
import numpy as np
import json
import csv
import osmium

CITY_BBOX = {
    "New York Manhattan": (40.70, -74.02, 40.88, -73.90),
    "London Camden": (51.52, -0.19, 51.56, -0.11),
    "London Canary Wharf": (51.49, -0.02, 51.51, 0.02),
    "Shanghai Pudong": (31.20, 121.45, 31.27, 121.55)
}

def load_glb(path):
    print(f"正在加载模型: {path}")
    scene = trimesh.load(path, force='scene')
    buildings = []
    centers = []

    for name, geom in scene.geometry.items():
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
    return buildings, centers

# =========================
# 从本地 OSM .pbf 文件解析建筑
# =========================
class BuildingHandler(osmium.SimpleHandler):
    """提取 OSM 中的所有建筑及其完整标签"""
    
    def __init__(self, bbox):
        super().__init__()
        self.minlat, self.minlon, self.maxlat, self.maxlon = bbox
        self.buildings = []
        self.count = 0
    
    def node(self, n):
        """处理节点建筑"""
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
                if self.count % 1000 == 0:
                    print(f"  已读取 {self.count} 个建筑...")
    
    def way(self, w):
        """处理路径建筑（大多数建筑）"""
        if 'building' in w.tags:
            try:
                # 计算中心点
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
                        if self.count % 1000 == 0:
                            print(f"  已读取 {self.count} 个建筑...")
            except Exception as e:
                pass

def fetch_osm_from_pbf(pbf_file, city, district):
    """从本地 .pbf 文件提取真实 OSM 建筑数据"""
    key = f"{city} {district}"
    if key not in CITY_BBOX:
        raise Exception(f"未定义区域bbox: {key}")

    bbox = CITY_BBOX[key]
    minlat, minlon, maxlat, maxlon = bbox
    
    print(f"正在从本地 OSM 文件解析建筑数据")
    print(f"文件: {pbf_file}")
    print(f"区域: {city} {district}")
    print("(扫描整个文件，可能需要 1-3 分钟...)\n")
    
    try:
        handler = BuildingHandler(bbox)
        handler.apply_file(pbf_file)
        
        buildings = handler.buildings
        
        if not buildings:
            raise Exception(
                f"未在指定区域找到建筑数据。\n"
                f"检查项:\n"
                f"1. .pbf 文件是否正确覆盖该区域\n"
                f"2. 坐标范围是否设置正确: {bbox}"
            )
        
        print(f"✓ 成功解析 {len(buildings)} 个真实 OSM 建筑\n")
        return buildings
        
    except FileNotFoundError:
        raise Exception(f"OSM 文件不存在: {pbf_file}\n请先下载: https://download.geofabrik.de/")
    except Exception as e:
        raise Exception(f"解析 OSM 文件失败: {e}")

# =========================
# 匹配引擎
# =========================
def match_engine(glb_buildings, osm_buildings):
    """匹配 GLB 建筑到 OSM 建筑"""
    glb_centers = np.array([b["center"] for b in glb_buildings])
    g_min, g_max = glb_centers.min(axis=0), glb_centers.max(axis=0)
    
    print(f"正在匹配:")
    print(f"  GLB 建筑: {len(glb_buildings)} 个")
    print(f"  OSM 建筑: {len(osm_buildings)} 个\n")
    
    results = []
    matched_count = 0
    
    for idx, g in enumerate(glb_buildings):
        # 归一化 GLB 坐标到 0-1
        norm_g = (g["center"] - g_min) / (g_max - g_min + 1e-9)
        
        best_match = None
        min_dist = float('inf')

        # 寻找最近的 OSM 建筑
        for o in osm_buildings:
            dist = np.linalg.norm(norm_g - o["center"])
            if dist < min_dist:
                min_dist = dist
                best_match = o
        
        # 只保留距离足够近的匹配
        if min_dist < 0.15 and best_match:
            results.append({
                "glb_id": g["id"],
                "osm": best_match,
                "distance": float(min_dist)
            })
            matched_count += 1
        
        if (idx + 1) % 2000 == 0:
            print(f"  已处理: {idx + 1}/{len(glb_buildings)}, "
                  f"已匹配: {matched_count}")
    
    print(f"\n✓ 匹配完成: {matched_count}/{len(glb_buildings)} 个\n")
    return results

# =========================
# 材料推断
# =========================
def infer_material(tags):
    if not tags:
        return "unknown"
    
    # 优先使用 OSM 标签中的材料信息
    material = tags.get("building:material", "").lower()
    if material:
        return material
    
    # 根据建筑类型推断
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
def export_results(results):
    """导出为 CSV 和 JSON"""
    csv_rows = []
    json_data = {}

    for r in results:
        o = r.get("osm")
        if not o:
            continue

        glb_id = r["glb_id"]
        tags = o.get("tags", {})
        name = tags.get("name", "unnamed")
        btype = tags.get("building", "unknown")
        material = infer_material(tags)

        # 完整的信息：包括所有 OSM 标签
        json_data[str(glb_id)] = {
            "glb_id": glb_id,
            "osm_id": o.get("osm_id"),
            "osm_type": o.get("osm_type"),
            "name": name,
            "building_type": btype,
            "material": material,
            "match_distance": round(r["distance"], 4),
            "osm_tags": tags  # ← 保留所有 OSM 标签，没有任何丢失
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

    # 写 JSON（包含完整的 OSM 标签）
    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)

    print(f"✓ 导出完成: {len(csv_rows)} 条记录")
    print(f"  📄 result.csv (简洁格式)")
    print(f"  📋 result.json (包含所有 OSM 标签)")
    print(f"\n数据来源: 本地 OSM 数据")
    print(f"✓ 所有建筑名称、材料等信息已完整保留")

# =========================
# 主程序
# =========================
def run(glb_path, pbf_file, city, district):
    try:
        print("=" * 70)
        print("  GLB-OSM 建筑匹配引擎")
        print("  (本地 OSM 数据 - 完全离线)")
        print("=" * 70 + "\n")
        
        # 1. 加载 GLB
        glb_list, centers = load_glb(glb_path)
        print(f"✓ GLB加载成功: {len(glb_list)} 个建筑体\n")

        # 2. 从本地 .pbf 文件解析真实 OSM 建筑
        osm_list = fetch_osm_from_pbf(pbf_file, city, district)

        # 3. 空间匹配
        matches = match_engine(glb_list, osm_list)

        # 4. 导出结果
        export_results(matches)
        
        print("\n" + "=" * 70)
        print("  ✓✓✓ 任务完成！")
        print("=" * 70)

    except Exception as e:
        print(f"\n❌ 错误: {e}\n")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 5:
        print("用法: python script.py <model.gltf> <osm.pbf> <City> <District>")
        print("\n示例:")
        print('  python script.py Untitled.gltf new-york.pbf "New York" "Manhattan"')
        print('  python script.py model.gltf london.pbf "London" "Camden"')
        print("\n下载 OSM 数据:")
        print("  https://download.geofabrik.de/")
    else:
        run(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
