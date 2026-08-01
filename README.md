# MapMatching

Need to download the xxx.osm.pbf map first from https://download.geofabrik.de/。中国江苏的地图从这里下载https://download.geofabrik.de/asia/china/jiangsu.html/。


This map file is very big about 500MB, which is why not pushed to github.com

# How to use the code: “osm_pbf_to_buildings_gltf_plus_json3.py”

An example of command line as below:
没有地形高低起伏的（python osm_pbf_to_buildings_gltf_plus_json3.py ../Maps/jiangsu260603.osm.pbf buildings.glb --format glb --bbox 32.0000 118.7000 32.0900 118.8600 --material-mode infer）

包含地形起伏高低（python3 osm_pbf_DEM4_precise_soil_fill2.py ../Maps/chongqing-260703.osm.pbf output_soil_fill.glb --bbox 29.5410 106.5238 29.5865 106.5948 --material-mode infer --download-dem --dem-type COP30 --terrain-grid 200）

输出是模型文件building.glb 和模型中的建筑物属性文件buildings_extras.json

模型可以用这个网站导入模型：https://threejs.org/editor/，用高德地图验证是否正确：https://www.amap.com/search?query=%E5%8D%97%E4%BA%AC&city=110000&geoobj=115.41888%7C39.294693%7C118.249433%7C40.571008&zoom=9.04 （定位南京）

1. 选择区域，根据经纬度生成glb，gltf（和bin）的地图文件
--bbox 32.0000 118.7000 32.0900 118.8600，南京市新街口附件区域。
经纬度的格式是： 
bbox = (min_lon, min_lat, max_lon, max_lat)，最小经度，最小纬度，最大经度，最大纬度

另外还有一下的经纬度方便测试 （用的时候去掉逗号）：
    "New York Manhattan": (40.70 -74.02 40.72 -73.99), (建筑很多，程序比较慢)
    "London Camden": (51.52 -0.19 51.56 -0.11),
    "London Canary Wharf": (51.49 -0.02 51.51 0.02),
    "Shanghai Pudong": (31.20 121.45 31.27 121.55),
    "Nanjing Jiangning": (31.63 118.42 32.10 119.05),
    "Nanjing Xinjiekou": (32.0000 118.7000 32.0900 118.8600),
    "Nanjing Baijiahu": (31.9100 118.7750 31.9700 118.8500)
    "nanjing zijinshan" : (118.825 32.032 118.8655 32.0605)
    "chongqing": (29.5410 106.5238 29.5865 106.5948)
    "Edinburgh": (55.9429 -3.1752 55.9609 -3.2074)

国内地图的经纬度一般可以从AI中直接问出来，也可以从高德的地图找：https://lbs.amap.com/demo/javascript-api/example/3d/map3d。在右侧的HTML脚本中有这个function mapInit()， 其中center:[116.333926,39.997245] 就是地图任意位置的经纬度值。

2. 选择输出 .glb 还是 .gltf
--format glb, 或者 --format gltf
--download-dem：按你输入的 bbox 从 OpenTopography 下载裁剪好的 DEM GeoTIFF
--dem path.tif：使用已有 DEM
--terrain-grid 160 的意思是：
把整个 bbox 地形切成 160 x 160 个小格子
--terrain-grid 60    快速测试
--terrain-grid 80    比较快，适合大区域
--terrain-grid 120   平衡
--terrain-grid 160   细节较好，但慢
--terrain-grid 200+  更细，但不建议大区域直接用


3. 给建筑物赋材料
--material-mode infer
OSM 里没有 material 时，JSON 里标明 unknown，也可以选择按建筑类型推测一个材质
OSM 的确经常没有建筑材料信息。很多建筑只有：

building=yes
building=residential
building:levels=6
height=20
name=...

但没有：

building:material
facade:material
material
wall:material
roof:material

这里的处理方式是：
--material-mode actual：只使用 OSM 真实材料，没有就 unknown
--material-mode infer：没有真实材料时，根据 building=* 推测，比如 residential -> concrete

在gltf中还有buildings_extras.json中直接把
        "inferred_material": "brick",
        "material_inference_source": "default_brick",
        "dielectric_constant": 4.44,
        "conductivity_s_per_m": 0.018,
写入到gltf中的extras扩展字段中，也写在生成单独的JSON文件。 


4. 另外黑色/背景剩余区域不生成几何,或者被土壤填充：
只在 _extras.json 里记录这些（未）建模背景区域按 soil 处理，比如：
"remaining_ground_generated": false,
"unmodeled_background_material": "soil",
"unmodeled_background_note": "No ground mesh is generated in DEM mode unless 


5. 另外还有一些参数可以调整， 比如用一下命令运行：
python3 osm_pbf_DEM4_precise_soil_fill.py input.osm.pbf output.glb \
  --bbox minLat minLon maxLat maxLon \
  --dem your_dem.tif \
  --soil-fill-mode precise \
  --soil-road-margin 0.5 \
  --soil-min-area 0.25 \
  --soil-simplify 0.05 \
  --terrain-offset -0.02
参数意思：
--soil-fill-mode precise：只填建筑、道路、水体、植被等之间的真实空白区域。
--soil-road-margin 0.5：道路边缘额外扣掉 0.5 米，减少道路旁黑缝。
--soil-min-area 0.25：小于 0.25 平方米的碎土壤面不要，减少碎片。
--soil-simplify 0.05：轻微简化土壤边界，单位米。
--terrain-offset -0.02：土壤面比道路/建筑/植被略低 2 cm，减少闪烁或重叠面。

-------------------------------------------------------
另外"osm_type": "way" 表示这个对象在 OSM 里的原始类型是 way。
way：一串 OSM 点连成的线或面
relation multipolygon：多个 way 拼起来形成的复杂面
他们的关系是：
way = 基础零件
multipolygon relation = 用多个 way 拼出来的大区域

"osm_type": "way"
表示普通 OSM way。

"osm_type": "relation"
表示来自 relation multipolygon，比如复杂湖泊、水库、公园等。

"osm_type": "generated"
表示程序生成的默认 bbox 地面 bbox_ground。
------------------------------------------------------

# 一些材料的颜色列表如下：
water  水体   -> 不透明亮蓝色
land   土壤   -> 棕土色
grass  草地   -> 亮绿色
forest 森林   -> 深绿色
road   道路   -> 中灰色
sand   沙地   -> 沙黄色


# 其他的python code还在开发中，比如glb_geo_mapper.py等同名文件
下载几个大概几平方公里的地图，glb格式，可以用blender看到建筑物的模型的。https://threejs.org/editor/， 这个网站看模型也简单好用。但些文件中通常不包含建筑物类型和名称，所以不知道建筑物的材料和介电常数电导率。于是可以从网上查一下建筑物的类型和名称，就是网页上看到的地图中的建筑物名称。现在可以用AI编这个程序，把模型中的每一个建筑物的名字材料从地图上找出来。
但是，现在的有的代码是从xxx.osm.pbf中找建筑的材料，建筑名称。但xxx.osm.pbf中有的建筑含有，有的建筑没有。国外的地图包含建筑材料全一些。

