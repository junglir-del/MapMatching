# MapMatching

Need to download the xxx.osm.pbf map first from https://download.geofabrik.de/. 

This map file is very big about 500MB, which is why not pushed to github.com

# How to use the code: “osd_pbf_to_buildings+glb.py”
# Example of command line as below:
python osm_pbf_to_buildings_glb2.py ../Maps/jiangsu260603.osm.pbf buildings.glb --format glb --bbox 32.0000 118.7000 32.0900 118.8600 --material-mode infer

输出是模型文件building.glb or .gltf(和bin) 和模型中的建筑物属性文件buildings_metadata.json

模型可以用这个网站导入模型：https://threejs.org/editor/，用高德地图验证是否正确：https://www.amap.com/search?query=%E5%8D%97%E4%BA%AC&city=110000&geoobj=115.41888%7C39.294693%7C118.249433%7C40.571008&zoom=9.04 （定位南京）

1. 选择区域，根据经纬度生成glb，gltf（和bin）的地图文件
--bbox 32.0000 118.7000 32.0900 118.8600，南京市新街口附件区域。
经纬度的格式是： 
bbox = (min_lon, min_lat, max_lon, max_lat)，最小经度，最小纬度，最大经度，最大纬度

另外还有一下的经纬度方便测试 （用的时候去掉逗号）：
    "New York Manhattan": (40.70, -74.02, 40.72, -73.99), (建筑很多，程序比较慢)
    "London Camden": (51.52, -0.19, 51.56, -0.11),
    "London Canary Wharf": (51.49, -0.02, 51.51, 0.02),
    "Shanghai Pudong": (31.20, 121.45, 31.27, 121.55),
    "Nanjing Jiangning": (31.63, 118.42, 32.10, 119.05),
    "Nanjing Xinjiekou": (32.0000, 118.7000, 32.0900, 118.8600),
    "Nanjing Baijiahu": (31.9100, 118.7750, 31.9700, 118.8500)

国内地图的经纬度一般可以从AI中直接问出来，也可以从高德的地图找：https://lbs.amap.com/demo/javascript-api/example/3d/map3d。在右侧的HTML脚本中有这个functions， center:[116.333926,39.997245] 就是地图任意位置的经纬度值。

function mapInit(){
  map = new AMap.Map('container', {
    resizeEnable: true,
    rotateEnable:true,
    pitchEnable:true,
    zoom: 17,
    pitch:80,
    rotation:-15,
    viewMode:'3D',//开启3D视图,默认为关闭
    buildingAnimation:true,//楼块出现是否带动画
    
    expandZoomRange:true,
    zooms:[3,20],
    center:[116.333926,39.997245]
  });

2. 选择输出 .glb 还是 .gltf
--format glb

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
JSON 里同时写：
actual_material
resolved_material
material_source

# 其他的python code还在开发中，比如glb_geo_mapper.py等同名文件
下载几个大概几平方公里的地图，glb格式，可以用blender看到建筑物的模型的。https://threejs.org/editor/， 这个网站简单好用。但些文件中通常不包含建筑物类型和名称，所以不知道建筑物的材料和介电常数电导率。于是可以从网上查一下建筑物的类型和名称，就是网页上看到的地图中的建筑物名称。现在可以用AI编这个程序，把模型中的每一个建筑物的名字材料从地图上找出来。
但是，现在的有的代码是从xxx.osm.pbf中找建筑的材料，建筑名称。但xxx.osm.pbf中有的建筑含有，有的建筑没有。国外的地图包含建筑材料全一些。但国内的不全，或者很少。