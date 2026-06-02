import trimesh
import numpy as np
import requests
import json
import csv
import time

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
# 方案：使用 Nominatim 反向地理编码
# =========================
def fetch_osm_nominatim(city, district):
    """
    使用 Nominatim 的反向地理编码查询建筑信息
    更稳定、不容易限流
    """
    key = f"{city} {district}"
    if key not in CITY_BBOX:
        raise Exception(f"未定义区域bbox: {key}")

    minlat, minlon, maxlat, maxlon = CITY_BBOX[key]
    
    buildings = []
    
    # 在区域内生成网格，逐个查询
    # 步长越小，查询越细致但速度越慢
    grid_step = 0.005  # 约 500 米
    
    lat_points = np.arange(minlat, maxlat, grid_step)
    lon_points = np.arange(minlon, maxlon, grid_step)
    
    total_points = len(lat_points) * len(lon_points)
    print(f"将在网格中查询 {total_points} 个点的建筑信息...\n")
    
    nominatim_url = "https://nominatim.openstreetmap.org/reverse"
    
    count = 0
    for i, lat in enumerate(lat_points):
        for j, lon in enumerate(lon_points):
            count += 1
            
            try:
                params = {
                    "lat": lat,
                    "lon": lon,
                    "format": "json",
                    "zoom": 18,  # 建筑级别
                    "addressdetails": 1,
                    "extratags": 1
                }
                
                # 查询这个点
                response = requests.get(
                    nominatim_url,
                    params=params,
                    timeout=30,
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # 检查是否是建筑
                    osm_type = data.get("osm_type")
                    extra_tags = data.get("extratags", {})
                    address = data.get("address", {})
                    
                    # 只要有 building 标签的
                    if extra_tags.get("building") or address.get("building"):
                        osm_id = data.get("osm_id")
                        
                        # 避免重复
                        if not any(b["id"] == osm_id for b in buildings):
                            norm_x = (lon - minlon) / (maxlon - minlon + 1e-9)
                            norm_y = (lat - minlat) / (maxlat - minlat + 1e-9)
                            
                            buildings.append({
                                "id": osm_id,
                                "center": np.array([norm_x, norm_y]),
                                "tags": {
                                    "name": extra_tags.get("name") or address.get("building", "unknown"),
                                    "building": extra_tags.get("building", "yes"),
                                    "building:material": extra_tags.get("building:material", ""),
                                    "building:levels": extra_tags.get("building:levels", ""),
                                }
                            })
                
                if count % 50 == 0:
                    print(f"  已扫描 {count}/{total_points} 个点，找到 {len(buildings)} 个建筑")
                
                # 避免限流：Nominatim 要求 1 秒最多 1 次请求
                time.sleep(1.1)
                
            except requests.Timeout:
                continue
            except Exception as e:
                continue
    
    if not buildings:
        raise Exception("未找到任何建筑。请检查坐标范围或网络连接。")
    
    print(f"\n✓ 总共找到 {len(buildings)} 个建筑\n")
    return buildings

# =========================
# 匹配引擎
# =========================
def match_engine(glb_buildings, osm_buildings):
    """匹配 GLB 建筑到 OSM 建筑"""
    glb_centers = np.array([b["center"] for b in glb_buildings])
    g_min, g_max = glb_centers.min(axis=0), glb_centers.max(axis=0)
    
    results = []
    print(f"正在匹配 {len(glb_buildings)} 个 GLB 建筑到 {len(osm_buildings)} 个 OSM 建筑...")
    print("(这可能需要几分钟...)\n")
    
    for idx, g in enumerate(glb_buildings):
        norm_g = (g["center"] - g_min) / (g_max - g_min + 1e-9)
        
        best_match = None
        min_dist = float('inf')

        for o in osm_buildings:
            dist = np.linalg.norm(norm_g - o["center"])
            if dist < min_dist:
                min_dist = dist
                best_match = o
        
        # 只保留距离接近的匹配
        if min_dist < 0.15 and best_match:
            results.append({
                "glb_id": g["id"],
                "osm": best_match,
                "distance": float(min_dist)
            })
        
        if (idx + 1) % 2000 == 0:
            print(f"  已处理 {idx + 1}/{len(glb_buildings)} 个")
    
    print(f"✓ 成功匹配 {len(results)} 个\n")
    return results

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

        json_data[str(glb_id)] = {
            "osm_id": o.get("id"),
            "name": name,
            "type": btype,
            "material": material,
            "match_distance": round(r["distance"], 4)
        }
        csv_rows.append([str(glb_id), str(o.get("id")), name, btype, material])

    # 写 CSV
    with open("result.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["GLB_ID", "OSM_ID", "NAME", "TYPE", "MATERIAL"])
        writer.writerows(csv_rows)

    # 写 JSON
    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)

    print(f"✓ 导出完成: {len(csv_rows)} 条记录")
    print(f"  📄 result.csv")
    print(f"  📋 result.json")

# =========================
# 主程序
# =========================
def run(glb_path, city, district):
    try:
        print("=" * 70)
        print("  GLB-OSM 建筑匹配引擎 (使用 Nominatim API)")
        print("=" * 70 + "\n")
        
        # 加载 GLB
        glb_list, centers = load_glb(glb_path)
        print(f"✓ GLB加载成功: {len(glb_list)} 个建筑体\n")

        # 获取 OSM 数据（使用 Nominatim）
        print("正在从 OpenStreetMap 查询建筑数据...")
        print("(Nominatim API 限制: 每秒最多 1 次请求，会持续几分钟)\n")
        osm_list = fetch_osm_nominatim(city, district)

        # 匹配
        matches = match_engine(glb_list, osm_list)

        # 导出
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
    if len(sys.argv) < 4:
        print("用法: python script.py <model.gltf> <City> <District>")
        print("示例: python script.py Untitled.gltf \"New York\" \"Manhattan\"")
    else:
        run(sys.argv[1], sys.argv[2], sys.argv[3])
