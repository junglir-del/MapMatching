import trimesh
import numpy as np
import json
import csv
import osmium
import time
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from tqdm import tqdm

CITY_BBOX = {
    "New York Manhattan": (40.70, -74.02, 40.88, -73.90),
    "London Camden": (51.52, -0.19, 51.56, -0.11),
    "London Canary Wharf": (51.49, -0.02, 51.51, 0.02),
    "Shanghai Pudong": (31.20, 121.45, 31.27, 121.55),
    "Nanjing Jiangning": (31.63, 118.42, 32.10, 119.05),
    "Nanjing Xinjiekou": (32.0000, 118.7000, 32.0900, 118.8600),
    "Nanjing Baijiahu": (31.9100, 118.7750, 31.9700, 118.8500)

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
    
    for name, geom in tqdm(scene.geometry.items(), desc="   提取建筑数据", 
                           total=total_geoms, unit="个"):
        try:
            c = np.array(geom.centroid)
            ground_center = np.array([c[0], c[2]])
            buildings.append({
                "id": name, 
                "center": ground_center,
                "geometry": geom
            })
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
                        
                        # 提取建筑的坐标
                        building_lats = np.array(lats)
                        building_lons = np.array(lons)
                        norm_lons = (building_lons - self.minlon) / (self.maxlon - self.minlon + 1e-9)
                        norm_lats = (building_lats - self.minlat) / (self.maxlat - self.minlat + 1e-9)
                        
                        self.buildings.append({
                            "id": f"way_{w.id}",
                            "osm_id": w.id,
                            "osm_type": "way",
                            "center": np.array([norm_x, norm_y]),
                            "coordinates": list(zip(norm_lons, norm_lats)),
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
                "distance": float(min_dist),
                "glb_center": norm_aligned[idx].tolist(),
                "osm_center": best_match["center"].tolist()
            })
            matched_count += 1
    
    match_rate = (matched_count / len(glb_buildings) * 100) if glb_buildings else 0
    return results, match_rate, matched_count

# =========================
# 自动检测最优旋转角度
# =========================
def auto_detect_rotation(glb_buildings, osm_buildings, coarse_step=15, fine_step=1):
    """
    两阶段搜索最优旋转角度
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
    
    # 第二阶段：细搜索
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
# 3D 可视化：GLB Model
# =========================
def visualize_glb_model(glb_buildings, alignment_config, output_file="glb_model_3d.html"):
    """
    直接画 GLB 中的 3D 建筑模型
    """
    print("📸 正在渲染 GLB 建筑模型...")
    
    fig = go.Figure()
    
    glb_centers = np.array([b["center"] for b in glb_buildings])
    
    # 应用对齐变换
    aligned_centers = alignment_config.apply(glb_centers)
    min_coords = aligned_centers.min(axis=0)
    max_coords = aligned_centers.max(axis=0)
    norm_aligned = (aligned_centers - min_coords) / (max_coords - min_coords + 1e-9)
    
    # 绘制每个建筑
    for i, building in enumerate(tqdm(glb_buildings, desc="   渲染建筑", unit="个")):
        try:
            geom = building["geometry"]
            
            if isinstance(geom, trimesh.Trimesh):
                vertices = geom.vertices
                faces = geom.faces
                
                # 应用对齐变换
                vertices_2d = vertices[:, [0, 2]]
                transformed = alignment_config.apply(vertices_2d)
                transformed = (transformed - min_coords) / (max_coords - min_coords + 1e-9)
                
                # 高度从 Y 坐标（模型中是高度）
                heights = vertices[:, 1]
                
                # 添加到图形
                fig.add_trace(go.Scatter3d(
                    x=transformed[:, 0],
                    y=transformed[:, 1],
                    z=heights,
                    mode='markers',
                    marker=dict(size=2, color='steelblue', opacity=0.6),
                    hoverinfo='skip',
                    showlegend=False
                ))
        except:
            pass
    
    fig.update_layout(
        title=dict(
            text=f"<b>GLB 建筑模型</b><br><sub>旋转: {alignment_config.rotation_rad * 180 / np.pi:.1f}° | 总计: {len(glb_buildings)} 个建筑</sub>",
            font=dict(size=16)
        ),
        scene=dict(
            xaxis_title='X 坐标',
            yaxis_title='Y 坐标',
            zaxis_title='高度',
            camera=dict(eye=dict(x=1.5, y=1.5, z=1.3)),
            xaxis=dict(backgroundcolor='rgb(240, 240, 240)'),
            yaxis=dict(backgroundcolor='rgb(240, 240, 240)'),
            zaxis=dict(backgroundcolor='rgb(240, 240, 240)')
        ),
        width=1200,
        height=800,
        hovermode='closest',
        font=dict(size=12)
    )
    
    fig.write_html(output_file)
    print(f"✓ GLB 模型已保存: {output_file}\n")


# =========================
# 3D 可视化：OSM 地图
# =========================
def visualize_osm_map(osm_buildings, output_file="osm_map_3d.html"):
    """
    直接画 OSM 地图上的建筑
    """
    print("📸 正在渲染 OSM 地图...")
    
    fig = go.Figure()
    
    # 绘制每个 OSM 建筑
    for building in tqdm(osm_buildings, desc="   渲染建筑", unit="个"):
        try:
            center = building["center"]
            
            # 绘制建筑中心
            fig.add_trace(go.Scatter3d(
                x=[center[0]],
                y=[center[1]],
                z=[0],
                mode='markers',
                marker=dict(size=4, color='steelblue', opacity=0.7),
                hovertext=f"{building['tags'].get('name', 'unnamed')}",
                hoverinfo='text',
                showlegend=False
            ))
            
            # 如果有建筑的轮廓坐标，就绘制多边形
            if "coordinates" in building and len(building["coordinates"]) > 2:
                coords = np.array(building["coordinates"])
                # 闭合多边形
                coords = np.vstack([coords, coords[0]])
                
                fig.add_trace(go.Scatter3d(
                    x=coords[:, 0],
                    y=coords[:, 1],
                    z=[0] * len(coords),
                    mode='lines',
                    line=dict(color='steelblue', width=2),
                    hoverinfo='skip',
                    showlegend=False
                ))
        except:
            pass
    
    fig.update_layout(
        title=dict(
            text=f"<b>OSM 地图</b><br><sub>总计: {len(osm_buildings)} 个建筑</sub>",
            font=dict(size=16)
        ),
        scene=dict(
            xaxis_title='经度',
            yaxis_title='纬度',
            zaxis_title='',
            camera=dict(eye=dict(x=1.5, y=1.5, z=1.3)),
            xaxis=dict(backgroundcolor='rgb(240, 240, 240)'),
            yaxis=dict(backgroundcolor='rgb(240, 240, 240)'),
            zaxis=dict(backgroundcolor='rgb(240, 240, 240)')
        ),
        width=1200,
        height=800,
        hovermode='closest',
        font=dict(size=12)
    )
    
    fig.write_html(output_file)
    print(f"✓ OSM 地图已保存: {output_file}\n")


# =========================
# 并排对比：Model vs Map
# =========================
def visualize_model_vs_map(glb_buildings, osm_buildings, alignment_config, 
                          output_file="model_vs_map.html"):
    """
    并排显示 GLB Model 和 OSM Map
    """
    print("📸 正在生成对比图...\n")
    
    # 创建子图
    fig = make_subplots(
        rows=1, cols=2,
        specs=[[{'type': 'scatter3d'}, {'type': 'scatter3d'}]],
        subplot_titles=('GLB 建筑模型', 'OSM 地图')
    )
    
    # ========== 左图：GLB Model ==========
    glb_centers = np.array([b["center"] for b in glb_buildings])
    aligned_centers = alignment_config.apply(glb_centers)
    min_coords = aligned_centers.min(axis=0)
    max_coords = aligned_centers.max(axis=0)
    norm_aligned = (aligned_centers - min_coords) / (max_coords - min_coords + 1e-9)
    
    for building in glb_buildings:
        try:
            geom = building["geometry"]
            if isinstance(geom, trimesh.Trimesh):
                vertices = geom.vertices
                vertices_2d = vertices[:, [0, 2]]
                transformed = alignment_config.apply(vertices_2d)
                transformed = (transformed - min_coords) / (max_coords - min_coords + 1e-9)
                heights = vertices[:, 1]
                
                fig.add_trace(go.Scatter3d(
                    x=transformed[:, 0],
                    y=transformed[:, 1],
                    z=heights,
                    mode='markers',
                    marker=dict(size=2, color='steelblue', opacity=0.6),
                    hoverinfo='skip',
                    showlegend=False
                ), row=1, col=1)
        except:
            pass
    
    # ========== 右图：OSM Map ==========
    for building in osm_buildings:
        try:
            center = building["center"]
            
            fig.add_trace(go.Scatter3d(
                x=[center[0]],
                y=[center[1]],
                z=[0],
                mode='markers',
                marker=dict(size=3, color='steelblue', opacity=0.7),
                hoverinfo='skip',
                showlegend=False
            ), row=1, col=2)
            
            if "coordinates" in building and len(building["coordinates"]) > 2:
                coords = np.array(building["coordinates"])
                coords = np.vstack([coords, coords[0]])
                
                fig.add_trace(go.Scatter3d(
                    x=coords[:, 0],
                    y=coords[:, 1],
                    z=[0] * len(coords),
                    mode='lines',
                    line=dict(color='steelblue', width=2),
                    hoverinfo='skip',
                    showlegend=False
                ), row=1, col=2)
        except:
            pass
    
    fig.update_layout(
        title_text=f"<b>Model vs Map 对比</b> | GLB: {len(glb_buildings)} 个 | OSM: {len(osm_buildings)} 个",
        width=1800,
        height=700,
        font=dict(size=11)
    )
    
    fig.update_scenes(
        xaxis_title='X/经度',
        yaxis_title='Y/纬度',
        zaxis_title='高度/---',
        camera=dict(eye=dict(x=1.5, y=1.5, z=1.2))
    )
    
    fig.write_html(output_file)
    print(f"✓ 对比图已保存: {output_file}\n")


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
# 生成置信度报告
# =========================
def generate_confidence_report(matches):
    """生成匹配置信度报告"""
    
    report = "匹配置信度分析\n"
    report += "=" * 70 + "\n\n"
    
    sorted_matches = sorted(matches, key=lambda x: x['distance'])
    
    high_conf = [m for m in sorted_matches if m['distance'] < 0.05]
    medium_conf = [m for m in sorted_matches if 0.05 <= m['distance'] < 0.10]
    low_conf = [m for m in sorted_matches if m['distance'] >= 0.10]
    
    report += f"高置信度 (距离 < 0.05): {len(high_conf)} 个 ({len(high_conf)/len(matches)*100:.1f}%)\n"
    report += f"中置信度 (距离 0.05-0.10): {len(medium_conf)} 个 ({len(medium_conf)/len(matches)*100:.1f}%)\n"
    report += f"低置信度 (距离 >= 0.10): {len(low_conf)} 个 ({len(low_conf)/len(matches)*100:.1f}%)\n\n"
    
    with open("confidence_report.txt", "w", encoding="utf-8") as f:
        f.write(report)
    
    print("=" * 70)
    print("置信度分析:")
    print("=" * 70)
    print(f"高置信度 (< 0.05): {len(high_conf)} 个 ({len(high_conf)/len(matches)*100:.1f}%)")
    print(f"中置信度 (0.05-0.10): {len(medium_conf)} 个 ({len(medium_conf)/len(matches)*100:.1f}%)")
    print(f"低置信度 (>= 0.10): {len(low_conf)} 个 ({len(low_conf)/len(matches)*100:.1f}%)")
    print("=" * 70 + "\n")

# =========================
# 主程序
# =========================
def run(glb_path, pbf_file, city, district):
    try:
        print("\n" + "=" * 70)
        print("  GLB-OSM 建筑匹配引擎")
        print("  自动检测最优旋转角度 + 3D 可视化")
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

        # 6. 生成可视化
        print("\n🎨 正在生成 3D 可视化...\n")
        
        visualize_glb_model(glb_list, alignment_config)
        visualize_osm_map(osm_list)
        visualize_model_vs_map(glb_list, osm_list, alignment_config)
        
        generate_confidence_report(matches)
        
        print("\n" + "=" * 70)
        print("  ✓✓✓ 任务完成！")
        print("=" * 70)
        print("\n📁 生成的文件:")
        print("  📊 result.csv")
        print("  📋 result.json")
        print("  ⚙️  alignment_config.json")
        print("\n  🎨 3D 可视化 (用浏览器打开，可旋转、缩放、平移):")
        print("  ✅ glb_model_3d.html ⬅️ GLB 建筑模型")
        print("  ✅ osm_map_3d.html ⬅️ OSM 地图建筑")
        print("  ✅ model_vs_map.html ⬅️ Model vs Map 对比（推荐！）")
        print("\n  📝 confidence_report.txt\n")

    except Exception as e:
        print(f"\n❌ 错误: {e}\n")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 5:
        print("用法: python script.py <model.glb> <osm.pbf> <City> <District>")
        print("\n示例:")
        print('  python script.py model.glb london.pbf "London" "Camden"')
        print('  python script.py model.glb new-york.pbf "New York" "Manhattan"')
    else:
        run(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])