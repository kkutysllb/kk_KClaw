---
name: blender-mcp
description: 通过socket连接直接控制Blender。创建3D对象、材质、动画，运行任意Blender Python (bpy)代码。当用户想要在Blender中创建或修改任何内容时使用。
version: 1.0.0
requires: Blender 4.3+ (需要桌面实例，不支持无头模式)
author: alireza78a
tags: [blender, 3d, 动画, 建模, bpy, mcp]
---

# Blender MCP

通过TCP端口9876的socket从KClaw控制正在运行的Blender实例。

## 设置（一次性）

### 1. 安装Blender插件

    curl -sL https://raw.githubusercontent.com/ahujasid/blender-mcp/main/addon.py -o ~/Desktop/blender_mcp_addon.py

在Blender中：
    编辑 > 预设 > 插件 > 安装 > 选择blender_mcp_addon.py
    启用"界面：Blender MCP"

### 2. 在Blender中启动socket服务器

在Blender视图中按N打开侧边栏。
找到"BlenderMCP"标签并点击"启动服务器"。

### 3. 验证连接

    nc -z -w2 localhost 9876 && echo "OPEN" || echo "CLOSED"

## 协议

通过TCP发送纯UTF-8 JSON -- 无长度前缀。

发送:     {"type": "<command>", "params": {<kwargs>}}
接收:     {"status": "success", "result": <value>}
          {"status": "error",   "message": "<reason>"}

## 可用命令

| type                    | params            | description                     |
|-------------------------|-------------------|---------------------------------|
| execute_code            | code (str)        | 运行任意bpy Python代码          |
| get_scene_info          | (无)              | 列出场景中的所有对象            |
| get_object_info         | object_name (str) | 获取特定对象的详细信息          |
| get_viewport_screenshot | (无)              | 当前视口的截图                  |

## Python辅助函数

在execute_code工具调用中使用：

    import socket, json

    def blender_exec(code: str, host="localhost", port=9876, timeout=15):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((host, port))
        s.settimeout(timeout)
        payload = json.dumps({"type": "execute_code", "params": {"code": code}})
        s.sendall(payload.encode("utf-8"))
        buf = b""
        while True:
            try:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                try:
                    json.loads(buf.decode("utf-8"))
                    break
                except json.JSONDecodeError:
                    continue
            except socket.timeout:
                break
        s.close()
        return json.loads(buf.decode("utf-8"))

## 常见bpy模式

### 清除场景
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()

### 添加网格对象
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1, location=(0, 0, 0))
    bpy.ops.mesh.primitive_cube_add(size=2, location=(3, 0, 0))
    bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=2, location=(-3, 0, 0))

### 创建并分配材质
    mat = bpy.data.materials.new(name="MyMat")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (R, G, B, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.3
    bsdf.inputs["Metallic"].default_value = 0.0
    obj.data.materials.append(mat)

### 关键帧动画
    obj.location = (0, 0, 0)
    obj.keyframe_insert(data_path="location", frame=1)
    obj.location = (0, 0, 3)
    obj.keyframe_insert(data_path="location", frame=60)

### 渲染到文件
    bpy.context.scene.render.filepath = "/tmp/render.png"
    bpy.context.scene.render.engine = 'CYCLES'
    bpy.ops.render.render(write_still=True)

## 陷阱

- 运行前必须检查socket是否打开（nc -z localhost 9876）
- 每次会话都必须在Blender中启动插件服务器（N面板 > BlenderMCP > 连接）
- 将复杂场景分解成多个较小的execute_code调用以避免超时
- 渲染输出路径必须是绝对路径（/tmp/...）而不是相对路径
- shade_smooth()需要先选择对象并处于对象模式
