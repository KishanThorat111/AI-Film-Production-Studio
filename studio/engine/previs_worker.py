"""Blender worker (own process). Builds a plain set from numbers and renders it: python previs_worker.py job.json
One scale (metres), z up, grey materials, honest light. It shows where things are, not how the film will look."""
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
P, out = job["scene"], Path(job["out"])
out.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
S = bpy.context.scene
S.render.engine = "BLENDER_EEVEE"
W, H = job["size"]
S.render.resolution_x, S.render.resolution_y, S.render.resolution_percentage = W, H, 67 if job["moving"] else 100
S.render.fps = job["fps"]
S.view_settings.view_transform = "Standard"
S.render.image_settings.file_format = "PNG"
try:
    S.eevee.taa_render_samples = 16
except AttributeError:
    pass


def material(name, col, rough=.85, emit=None):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*col, 1)
    b.inputs["Roughness"].default_value = rough
    if emit:
        b.inputs["Emission Color"].default_value = (*emit, 1)
        b.inputs["Emission Strength"].default_value = 4
    return m


M = {"wall": material("wall", (.42, .42, .41)), "floor": material("floor", (.26, .24, .22)), "prop": material("prop", (.36, .34, .31)), "figure": material("figure", (.55, .5, .46)), "head": material("head", (.62, .52, .45)), "lamp": material("lamp", (.9, .85, .7), emit=(1, .85, .6))}


def add(o, m):
    o.data.materials.append(M[m])
    return o


def box(x, y, z, w, d, h, m="prop", rot=0.0):
    """x, y: centre; z: bottom."""
    bpy.ops.mesh.primitive_cube_add(size=1, location=(x, y, z + h / 2), rotation=(0, 0, math.radians(rot)))
    o = bpy.context.object
    o.scale = (w, d, h)
    return add(o, m)


def cyl(x, y, z, r, h, m="prop"):
    bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=h, location=(x, y, z + h / 2))
    return add(bpy.context.object, m)


def limb(a, b, r, m, parent):
    a, b = Vector(a), Vector(b)
    v = b - a
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1, segments=20, ring_count=12, location=(a + b) / 2)
    o = bpy.context.object
    o.scale = (r, r, v.length / 2 + r * .7)
    o.rotation_euler = v.to_track_quat("Z", "Y").to_euler()
    bpy.ops.object.shade_smooth()
    add(o, m).parent = parent


def wall(x0, y0, x1, y1, h, doors):
    """A wall from point to point with holes for doors: pieces beside and above each."""
    v = Vector((x1 - x0, y1 - y0))
    L, rot = v.length, math.degrees(math.atan2(v.y, v.x))
    u = v / L

    def piece(a, b, z, height):
        if b - a > .01 and height > .01:
            c = Vector((x0, y0)) + u * (a + b) / 2
            box(c.x, c.y, z, b - a, .1, height, "wall", rot)
    at = 0.0
    for d in sorted(doors, key=lambda d: d["at"]):
        a, b = d["at"], d["at"] + d.get("width", .9)
        piece(at, a, 0, h)
        piece(a, b, d.get("height", 2.0), h - d.get("height", 2.0))
        if d.get("sill"):
            piece(a, b, 0, d["sill"])
        at = b
    piece(at, L, 0, h)


for r in P.get("rooms", []):
    x, y, w, d, h = (float(r.get(k, v)) for k, v in (("x", 0), ("y", 0), ("w", 4), ("d", 4), ("h", 2.5)))
    box(x + w / 2, y + d / 2, -.05, w, d, .05, "floor")
    if r.get("ceiling", True):
        box(x + w / 2, y + d / 2, h, w, d, .05, "wall")
    dd = lambda s: [q for q in r.get("doors", []) if q.get("wall") == s]
    open_ = r.get("open", [])
    for s, c in (("s", (x, y, x + w, y)), ("n", (x, y + d, x + w, y + d)), ("w", (x, y, x, y + d)), ("e", (x + w, y, x + w, y + d))):
        if s not in open_:
            wall(*c, h, dd(s))
if not P.get("rooms"):
    box(0, 0, -.05, 60, 60, .05, "floor")
for q in P.get("props", []):
    x, y, z = (float(q.get(k, 0)) for k in ("x", "y", "z"))
    w, d, h, rot = (float(q.get(k, v)) for k, v in (("w", .6), ("d", .6), ("h", .6), ("rot", 0)))
    k = q.get("kind", "box")
    if k == "cyl":
        cyl(x, y, z, w / 2, h)
    elif k == "table":
        box(x, y, z + h - .04, w, d, .04, "prop", rot)
        for a in (-1, 1):
            for b in (-1, 1):
                v = Vector((a * (w / 2 - .05), b * (d / 2 - .05), 0))
                v.rotate(__import__("mathutils").Euler((0, 0, math.radians(rot))))
                box(x + v.x, y + v.y, z, .05, .05, h - .04, "prop", rot)
    elif k == "chair":
        box(x, y, z + .42, .45, .45, .05, "prop", rot)
        v = Vector((0, .2, 0))
        v.rotate(__import__("mathutils").Euler((0, 0, math.radians(rot))))
        box(x + v.x, y + v.y, z + .42, .45, .05, .5, "prop", rot)
        box(x, y, z, .3, .3, .42, "prop", rot)
    elif k == "bed":
        box(x, y, z, w, d, h * .7, "prop", rot)
        box(x, y, z + h * .7, w * .96, d * .96, h * .3, "wall", rot)
    elif k == "lamp":
        cyl(x, y, z, .04, h, "prop")
        bpy.ops.mesh.primitive_uv_sphere_add(radius=.1, location=(x, y, z + h))
        add(bpy.context.object, "lamp")
    else:
        box(x, y, z, w, d, h, "prop", rot)

POSE = {  # joints as fractions of height: hip, knee, foot, shoulder, elbow, hand, head; forward is +y
    "stand": dict(hip=(0, 0, .52), knee=(.0, .02, .27), foot=(0, 0, .02), sho=(0, 0, .82), elb=(.02, .0, .62), hand=(.04, .06, .45), head=(0, .01, .93)),
    "walk": dict(hip=(0, 0, .52), knee=(.0, .09, .28), foot=(0, .04, .02), sho=(0, .02, .82), elb=(.02, .05, .62), hand=(.03, .12, .48), head=(0, .03, .93)),
    "sit": dict(hip=(0, 0, .28), knee=(0, .25, .29), foot=(0, .26, .02), sho=(0, .0, .58), elb=(.03, .06, .42), hand=(.02, .2, .34), head=(0, .02, .69)),
    "kneel": dict(hip=(0, 0, .28), knee=(0, .1, .03), foot=(0, -.18, .03), sho=(0, .03, .58), elb=(.03, .08, .42), hand=(.02, .16, .3), head=(0, .05, .69)),
    "crouch": dict(hip=(0, -.04, .2), knee=(0, .16, .28), foot=(0, .02, .02), sho=(0, .12, .46), elb=(.03, .2, .32), hand=(.02, .26, .16), head=(0, .18, .56)),
    "lie": dict(hip=(0, 0, .06), knee=(0, -.25, .07), foot=(0, -.5, .04), sho=(0, .3, .07), elb=(.12, .16, .06), hand=(.14, 0, .05), head=(0, .43, .08))}
for f in P.get("figures", []):
    h, j = float(f.get("height", 1.7)), POSE.get(f.get("pose", "stand"), POSE["stand"])
    e = bpy.data.objects.new("f", None)
    S.collection.objects.link(e)
    g = lambda k, s: (j[k][0] * h * s + s * {"hip": .055, "knee": .06, "foot": .06, "sho": .11, "elb": .13, "hand": .12}.get(k, 0) * h, j[k][1] * h, j[k][2] * h)
    for s in (-1, 1):
        limb(g("hip", s), g("knee", s), .045 * h, "figure", e)
        limb(g("knee", s), g("foot", s), .034 * h, "figure", e)
        limb(g("sho", s), g("elb", s), .028 * h, "figure", e)
        limb(g("elb", s), g("hand", s), .023 * h, "figure", e)
    mid = lambda k: (0, j[k][1] * h, j[k][2] * h)
    limb(mid("hip"), mid("sho"), .085 * h, "figure", e)
    hd = mid("head")
    bpy.ops.mesh.primitive_uv_sphere_add(radius=.062 * h, location=hd)
    bpy.ops.object.shade_smooth()
    add(bpy.context.object, "head").parent = e
    bpy.ops.mesh.primitive_cone_add(radius1=.014 * h, radius2=0, depth=.035 * h, location=(hd[0], hd[1] + .07 * h, hd[2]), rotation=(-math.pi / 2, 0, 0))      # a nose, so that the direction of a face can be read
    add(bpy.context.object, "head").parent = e
    e.location, e.rotation_euler = (float(f.get("x", 0)), float(f.get("y", 0)), float(f.get("z", 0))), (0, 0, math.radians(float(f.get("rot", 0))))

w = bpy.data.worlds.new("w")
w.use_nodes = True
amb = float(P.get("ambient", .12 if P.get("lights") else .6))
w.node_tree.nodes["Background"].inputs[0].default_value = (.5 * amb, .53 * amb, .6 * amb, 1)
S.world = w
for l in P.get("lights", []):
    k = {"spot": "SPOT", "sun": "SUN"}.get(l.get("kind"), "POINT")
    d = bpy.data.lights.new("l", k)
    d.energy = float(l.get("power", 2 if k == "SUN" else 60))
    d.color = l.get("color", (1, .95, .85))
    if k == "SPOT":
        d.spot_size, d.spot_blend = math.radians(float(l.get("angle", 45))), .5
    o = bpy.data.objects.new("l", d)
    S.collection.objects.link(o)
    o.location = (float(l.get("x", 0)), float(l.get("y", 0)), float(l.get("z", 2)))
    if "tx" in l:
        o.rotation_euler = (Vector((l["tx"], l["ty"], l["tz"])) - o.location).to_track_quat("-Z", "Y").to_euler()

cam = bpy.data.cameras.new("c")
CAM = bpy.data.objects.new("c", cam)
S.collection.objects.link(CAM)
S.camera = CAM
cam.sensor_width = 36
A, B = P["camera"], P["camera"].get("to")


def set_camera(t):
    t = t * t * (3 - 2 * t)
    v = lambda k, d=0.0: float(A.get(k, d)) + ((float(B.get(k, A.get(k, d))) - float(A.get(k, d))) * t if B else 0)
    CAM.location = (v("x"), v("y"), v("z"))
    CAM.rotation_euler = (Vector((v("tx"), v("ty"), v("tz"))) - CAM.location).to_track_quat("-Z", "Y").to_euler()
    if A.get("roll"):
        CAM.rotation_euler.rotate_axis("Z", math.radians(float(A["roll"])))
    cam.lens = v("lens_mm", 35)


def depth_material(far):
    m = bpy.data.materials.new("depth")
    m.use_nodes = True
    n, L = m.node_tree.nodes, m.node_tree.links
    n.clear()
    o, em, cd, mr = n.new("ShaderNodeOutputMaterial"), n.new("ShaderNodeEmission"), n.new("ShaderNodeCameraData"), n.new("ShaderNodeMapRange")
    L.new(cd.outputs["View Z Depth"], mr.inputs[0])
    mr.inputs[1].default_value, mr.inputs[2].default_value, mr.inputs[3].default_value, mr.inputs[4].default_value = .2, far, 1, 0
    L.new(mr.outputs[0], em.inputs[0])
    L.new(em.outputs[0], o.inputs[0])
    return m


from bpy_extras.object_utils import world_to_camera_view as wc      # noqa: E402

n = P["frames"] if job["moving"] else 1
see = {}
for old in out.glob("*.png"):
    old.unlink()
for f in range(n):
    set_camera(f / max(1, n - 1))
    S.render.filepath = str(out / f"{f:04d}.png")
    bpy.ops.render.render(write_still=True)
    if f in (0, n - 1):
        bpy.context.view_layer.update()
        for i, g in enumerate(P.get("figures", [])):
            c = wc(S, CAM, Vector((float(g.get("x", 0)), float(g.get("y", 0)), float(g.get("z", 0)) + .85 * float(g.get("height", 1.7)))))
            see[f"{g.get('name', f'figure {i + 1}')} at frame {f}"] = bool(0 <= c.x <= 1 and 0 <= c.y <= 1 and c.z > 0)
set_camera(0)
far = max(4.0, min(40.0, (Vector((A["tx"], A["ty"], A["tz"])) - CAM.location).length * 2.2))
bpy.context.view_layer.material_override = depth_material(far)
S.render.resolution_percentage = 100
S.render.filepath = str(out / "depth.png")
bpy.ops.render.render(write_still=True)
(out / "visibility.json").write_text(json.dumps(see, indent=1))
print("DONE", n)
