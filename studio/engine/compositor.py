"""GPU compositor. 16-bit float, linear light. One small compute shader per effect; a closed list."""
from __future__ import annotations

import numpy as np
import wgpu

ENGINE_VERSION = "0.2.0"

HEAD = """
struct P { op:vec4f, size:vec4f, a:vec4f, b:vec4f, c:vec4f, d:vec4f };
@group(0) @binding(0) var<uniform> p:P;
@group(0) @binding(1) var src:texture_2d<f32>;
@group(0) @binding(2) var s:sampler;
@group(0) @binding(3) var dst:texture_storage_2d<rgba16float,write>;
@group(0) @binding(4) var aux:texture_2d<f32>;
fn S(uv:vec2f)->vec4f{ return textureSampleLevel(src,s,uv,0.); }
fn A(uv:vec2f)->vec4f{ return textureSampleLevel(aux,s,uv,0.); }
fn lum(c:vec3f)->f32{ return dot(c,vec3f(.2126,.7152,.0722)); }
fn h2(q:vec2f)->f32{ var v=fract(q*vec2f(.1031,.1030)); v+=dot(v,v.yx+33.33); return fract((v.x+v.y)*v.x); }
fn hu(v:vec3u)->f32{ var x=v.x*1664525u+v.y*22695477u+v.z*1103515245u+1013904223u; x^=x>>16u; x*=0x45d9f3bu; x^=x>>16u; x*=0x45d9f3bu; x^=x>>16u; return f32(x)/4294967295.; }
fn vn(q:vec2f)->f32{ let i=floor(q); let f=fract(q); let u=f*f*(3.-2.*f); return mix(mix(h2(i),h2(i+vec2f(1.,0.)),u.x),mix(h2(i+vec2f(0.,1.)),h2(i+vec2f(1.,1.)),u.x),u.y); }
"""
TAIL = """
@compute @workgroup_size(8,8) fn main(@builtin(global_invocation_id) g:vec3u){
 if(f32(g.x)>=p.size.x||f32(g.y)>=p.size.y){return;}
 let px=vec2f(g.xy)+.5; let uv=px/p.size.xy; let fr=p.size.z; let asp=p.size.x/p.size.y; let c0=S(uv);
 textureStore(dst,g.xy,fx(px,uv,fr,asp,c0)); }
"""
F = "fn fx(px:vec2f,uv:vec2f,fr:f32,asp:f32,c0:vec4f)->vec4f{"
# every effect: parameter names in the order they fill a,b,c,d (4 floats each), with defaults
OPS: dict[str, tuple[list[tuple[str, float]], str]] = {
    "place": ([("x", 0), ("y", 0), ("scale", 1), ("rot", 0), ("opacity", 1), ("fit_x", 1), ("fit_y", 1), ("ax", 0), ("ay", 0), ("lin", 0)], F + """
      let z=max(p.a.z,.0001); let r=radians(-p.a.w); var q=(uv-.5-p.a.xy)*vec2f(asp,1.); q-=vec2f(p.b.w*asp,p.c.x);
      q=vec2f(cos(r)*q.x-sin(r)*q.y, sin(r)*q.x+cos(r)*q.y)/z; q+=vec2f(p.b.w*asp,p.c.x);
      let t=q/vec2f(asp,1.)*p.b.yz+.5; let e=fwidthish(t); if(any(t<vec2f(0.))||any(t>vec2f(1.))){return vec4f(0.);} var c=S(t); if(p.c.y>.5){ c=vec4f(select(pow((c.rgb+.099)/1.099,vec3f(1./.45)),c.rgb/4.5,c.rgb<vec3f(.081)),c.a); } return c*vec4f(1.,1.,1.,p.b.x); }
      fn fwidthish(t:vec2f)->f32{return 0.;}"""),
    "over": ([("mode", 0), ("opacity", 1)], F + """
      let f=A(uv); let al=clamp(f.a*p.a.y,0.,1.); var m=f.rgb; let b=c0.rgb;
      if(p.a.x==1.){ return vec4f(b+f.rgb*al,max(c0.a,al)); } if(p.a.x==2.){ m=1.-(1.-clamp(b,vec3f(0.),vec3f(1.)))*(1.-clamp(f.rgb,vec3f(0.),vec3f(1.))); } if(p.a.x==3.){ m=b*f.rgb; }
      return vec4f(mix(b,m,al),max(c0.a,al)); }"""),
    "matte": ([("invert", 0)], F + "let k=A(uv).r; return vec4f(c0.rgb,c0.a*select(k,1.-k,p.a.x>.5)); }"),
    "grade": ([("exposure", 0), ("contrast", 1), ("saturation", 1), ("temp", 0), ("tint", 0), ("lift", 0), ("gamma", 1), ("pivot", .18), ("shadow_r", 0), ("shadow_g", 0), ("shadow_b", 0)], F + """
      var c=max(c0.rgb,vec3f(0.))*exp2(p.a.x); c*=vec3f(1.+.18*p.a.w,1.-.06*p.b.x,1.-.18*p.a.w); let pv=p.b.w;
      c=pv*pow(max(c,vec3f(1e-6))/pv,vec3f(p.a.y)); let l=lum(c); c=mix(vec3f(l),c,p.a.z); c=pow(max(c+p.b.y*(1.-clamp(l,0.,1.)),vec3f(0.)),vec3f(1./p.b.z));
      c+=p.c.xyz*(1.-smoothstep(0.,.25,l)); return vec4f(max(c,vec3f(0.)),c0.a); }"""),
    "blur": ([("radius", 4)], F + """
      if(p.a.x<.2){return c0;} var c=vec4f(0.); for(var i=0;i<64;i++){ let t=f32(i)*2.39996; let r=sqrt((f32(i)+.5)/64.)*p.a.x; c+=S(uv+vec2f(cos(t),sin(t))*r/p.size.xy); } return c/64.; }"""),
    "lens_blur": ([("focus", .5), ("radius", 10), ("range", .08)], F + """
      let d0=A(uv).r; let coc=clamp((abs(d0-p.a.x)-p.a.z)/.4,0.,1.)*p.a.y; if(coc<.3){return c0;} var c=vec4f(0.); var w=0.;
      for(var i=0;i<72;i++){ let t=f32(i)*2.39996; let r=sqrt((f32(i)+.5)/72.)*coc; let q=uv+vec2f(cos(t),sin(t))*r/p.size.xy; let v=S(q); let k=1.+3.*clamp(lum(v.rgb)-.6,0.,2.); c+=v*k; w+=k; } return c/w; }"""),
    "depth_parallax": ([("dx", 0), ("dy", 0), ("zoom", 0), ("focus", .5)], F + """
      var q=uv; for(var i=0;i<6;i++){ let d=A(q).r-p.a.w; q=uv-p.a.xy*d-(uv-.5)*p.a.z*(d+p.a.w); } return S(q); }"""),
    "beam": ([("x", .5), ("y", .5), ("radius", .22), ("soft", .5), ("ambient", .04), ("gain", 1.6), ("hot", .35), ("warm", .12), ("squash", 1), ("angle", 0), ("jitter", .004)], F + """
      let n=fr*.31; let j=(vec2f(vn(vec2f(n,3.)),vn(vec2f(n,9.)))-.5)*p.c.z; var q=(uv-p.a.xy-j)*vec2f(asp,1.); let r=radians(p.c.y); q=vec2f(cos(r)*q.x+sin(r)*q.y,-sin(r)*q.x+cos(r)*q.y); q.y*=p.c.x;
      let d=length(q)/max(p.a.z,.001); let k=1.-smoothstep(1.-p.a.w,1.,d); let hot=exp(-d*d*6.)*p.b.z; let ring=.06*exp(-pow((d-.82)*9.,2.));
      let L=p.b.x+(k*(.75+hot)+ring)*p.b.y; return vec4f(c0.rgb*L*mix(vec3f(1.),vec3f(1.+p.b.w,1.,1.-p.b.w),k),c0.a); }"""),
    "shaft": ([("x0", .5), ("y0", 1.1), ("x1", .5), ("y1", .3), ("w0", .02), ("w1", .22), ("gain", .06), ("warm", .1), ("noise", .6)], F + """
      let a=p.a.xy*vec2f(asp,1.); let b=p.a.zw*vec2f(asp,1.); let q=uv*vec2f(asp,1.); let d=b-a; let t=clamp(dot(q-a,d)/dot(d,d),0.,1.3); let e=length(q-a-d*t); let w=mix(p.b.x,p.b.y,t);
      let k=exp(-pow(e/max(w,.001),2.)*2.2)*(1.-smoothstep(.75,1.3,t)); let m=mix(1.,vn(uv*vec2f(7.,5.)+vec2f(fr*.012,-fr*.02))*1.6,p.c.x); return vec4f(c0.rgb+vec3f(1.+p.b.w,1.,1.-p.b.w)*p.b.z*k*m,c0.a); }"""),
    "dust": ([("gain", .5), ("density", 1), ("size", 1.3), ("drift_x", .004), ("drift_y", -.002), ("lit", .25)], F + """
      var add=0.; for(var l=0;l<3;l++){ let sc=(18.+f32(l)*15.)*p.a.y; let q=(uv*vec2f(asp,1.)+vec2f(p.a.w,p.b.x)*fr*.04*(1.+f32(l)*.5)+vec2f(vn(vec2f(fr*.01,f32(l)))*.02))*sc; let i=floor(q); let f=fract(q)-.5;
       let r=vec2f(h2(i+f32(l)*17.),h2(i+51.+f32(l)))-.5; let d=length((f-r*.8)/sc)*p.size.y; let on=step(.80,h2(i+7.*f32(l))); let tw=.55+.45*sin(fr*.09+h2(i)*40.); add+=on*tw*exp(-d*d/(p.a.z*p.a.z*(1.+f32(l)))); }
      let L=smoothstep(p.b.y*.2,p.b.y,lum(c0.rgb)); return vec4f(c0.rgb+vec3f(1.,.95,.88)*add*p.a.x*.12*L,c0.a); }"""),
    "flicker": ([("amount", .08), ("speed", 1), ("base", 1), ("seed", 0)], F + "let n=vn(vec2f(fr*.21*p.a.y,p.a.w))*.65+vn(vec2f(fr*.77*p.a.y,p.a.w+5.))*.35; return vec4f(c0.rgb*(p.a.z+(n-.5)*2.*p.a.x),c0.a); }"),
    "glow_pulse": ([("x", .5), ("y", .5), ("radius", .2), ("gain", .4), ("r", 1), ("g", .35), ("b", .08), ("pulse", .15), ("speed", .4)], F + """
      let d=length((uv-p.a.xy)*vec2f(asp,1.))/p.a.z; let n=1.+p.b.w*(vn(vec2f(fr*.1*p.c.x,2.))-.5)*2.; return vec4f(c0.rgb*(1.+p.b.xyz*exp(-d*d)*p.a.w*n*2.)+p.b.xyz*exp(-d*d*2.)*p.a.w*n*.08,c0.a); }"""),
    "shimmer": ([("x", .5), ("y", .4), ("rx", .2), ("ry", .25), ("amount", .0012), ("scale", 34)], F + """
      let e=(uv-p.a.xy)/p.a.zw; let k=exp(-dot(e,e)); let n=vec2f(vn(uv*p.b.y+vec2f(0.,fr*.35))-.5,vn(uv*p.b.y*1.3+vec2f(9.,fr*.29))-.5); return S(uv+n*p.b.x*k*2.); }"""),
    "vignette": ([("amount", .5), ("power", 2.2)], F + "let d=length((uv-.5)*vec2f(asp,1.))/length(vec2f(asp,1.)*.5); return vec4f(c0.rgb*(1.-p.a.x*pow(d,p.a.y)),c0.a); }"),
    "bloom": ([("threshold", .8), ("gain", .25), ("radius", 26)], F + """
      var c=vec3f(0.); for(var i=0;i<56;i++){ let t=f32(i)*2.39996; let r=sqrt((f32(i)+.5)/56.)*p.a.z; let v=S(uv+vec2f(cos(t),sin(t))*r/p.size.xy).rgb; c+=max(v-p.a.x,vec3f(0.)); } return vec4f(c0.rgb+c/56.*p.a.y*vec3f(1.,.82,.68),c0.a); }"""),
    "lens_distort": ([("k1", 0), ("k2", 0), ("zoom", 1), ("chroma", 0)], F + """
      let q=(uv-.5)*vec2f(asp,1.)*2./p.a.z; let r2=dot(q,q); let f=1.+p.a.x*r2+p.a.y*r2*r2; let k=q*f/vec2f(asp,1.)*.5; let e=k*p.a.w;
      let o=vec4f(S(k+.5+e).r,S(k+.5).g,S(k+.5-e).b,c0.a); let t=k+.5; if(any(t<vec2f(0.))||any(t>vec2f(1.))){return vec4f(0.,0.,0.,1.);} return o; }"""),
    "ir_look": ([("gain", 1.1), ("falloff", .7), ("noise", .03)], F + "let l=lum(c0.rgb)*p.a.x*(1.-p.a.y*pow(length((uv-.5)*vec2f(asp,1.)),2.)); let n=(hu(vec3u(vec2u(px),u32(fr)))-.5)*p.a.z; return vec4f(vec3f(max(l+n*.4,0.))*vec3f(.96,1.,.97),c0.a); }"),
    "compression": ([("block", 8), ("amount", .5), ("levels", 24)], F + """
      let b=p.a.x; let q=(floor(px/b)+.5)*b/p.size.xy; let avg=S(q); let c=mix(c0.rgb,avg.rgb,p.a.y*smoothstep(.25,0.,abs(lum(c0.rgb)-lum(avg.rgb)))); let g=pow(max(c,vec3f(0.)),vec3f(.4545)); return vec4f(pow(round(g*p.a.z)/p.a.z,vec3f(2.2))*.5+c*.5,c0.a); }"""),
    "grain": ([("amount", .03), ("size", 1.4), ("chroma", .25)], F + """
      let q=floor(px/p.a.y); let g=hu(vec3u(vec2u(q),u32(fr)*3u))+hu(vec3u(vec2u(q)+vec2u(91u,17u),u32(fr)*3u))-1.; let gc=vec3f(hu(vec3u(vec2u(q),u32(fr)*3u+1u)),hu(vec3u(vec2u(q)+31u,u32(fr)*3u+1u)),hu(vec3u(vec2u(q)+77u,u32(fr)*3u+2u)))-.5;
      let l=sqrt(max(lum(c0.rgb),0.)); let k=p.a.x*(.35+1.3*l*(1.-.6*l)); let d=sqrt(max(c0.rgb,vec3f(0.)))+(vec3f(g)+gc*p.a.z)*k; return vec4f(max(d,vec3f(0.))*max(d,vec3f(0.)),c0.a); }"""),
    "film_curve": ([("shoulder", .8), ("toe", .004), ("white", 4)], F + """
      var c=max(c0.rgb-p.a.y,vec3f(0.)); let w=p.a.z; let m=c*(1.+c/(w*w))/(1.+c*(1.-p.a.x*.75)); return vec4f(mix(c,m,smoothstep(vec3f(.25),vec3f(1.1),c)),c0.a); }"""),
    "fade": ([("level", 1)], F + "return vec4f(c0.rgb*p.a.x,c0.a); }"),
    "wipe": ([("progress", 0), ("angle", 0), ("soft", .06)], F + """
      let r=radians(p.a.y); let d=vec2f(cos(r),sin(r)); let e=abs(d.x)+abs(d.y); let t=dot(uv-.5,d)/e+.5; let s=max(p.a.z,.0005); return vec4f(c0.rgb,c0.a*(1.-smoothstep(p.a.x*(1.+s)-s,p.a.x*(1.+s),t))); }"""),
    "sharpen": ([("amount", .4), ("radius", 1.2)], F + """
      let o=p.a.y/p.size.xy; let n=(S(uv+vec2f(o.x,0.)).rgb+S(uv-vec2f(o.x,0.)).rgb+S(uv+vec2f(0.,o.y)).rgb+S(uv-vec2f(0.,o.y)).rgb)*.25; let d=clamp(c0.rgb-n,vec3f(-.12),vec3f(.12)); return vec4f(max(c0.rgb+d*p.a.x*2.,vec3f(0.)),c0.a); }"""),
    "halation": ([("threshold", .6), ("gain", .35), ("radius", 14)], F + """
      var h=0.; for(var i=0;i<40;i++){ let t=f32(i)*2.39996; let r=sqrt((f32(i)+.5)/40.)*p.a.z; h+=max(lum(S(uv+vec2f(cos(t),sin(t))*r/p.size.xy).rgb)-p.a.x,0.); } return vec4f(c0.rgb+vec3f(1.,.28,.08)*h/40.*p.a.y,c0.a); }"""),
    "chromatic": ([("amount", .002)], F + "let d=(uv-.5)*p.a.x; return vec4f(S(uv+d).r,c0.g,S(uv-d).b,c0.a); }"),
    "scanlines": ([("count", 480), ("amount", .3), ("roll", 0)], F + "let k=.5+.5*cos(6.28318*(uv.y*p.a.x+fr*p.a.z)); return vec4f(c0.rgb*(1.-p.a.y*(1.-k)),c0.a); }"),
    "letterbox": ([("ratio", 2.39)], F + "let hh=min(1.,asp/p.a.x); let out=abs(uv.y-.5)*2.>hh || abs(uv.x-.5)*2.>min(1.,p.a.x/asp); return select(c0,vec4f(0.,0.,0.,1.),out); }"),
    "fog": ([("density", .25), ("height", .55), ("scale", 3), ("drift", .004), ("r", .5), ("g", .55), ("b", .6), ("light", .25)], F + """
      let q=uv*vec2f(asp,1.)*p.a.z+vec2f(fr*p.a.w*p.a.z,fr*p.a.w*.3); let n=vn(q)*.5+vn(q*2.1+7.)*.3+vn(q*4.3+19.)*.2; let k=clamp(p.a.x*n*1.8*smoothstep(p.a.y-.45,p.a.y+.35,uv.y),0.,.95); return vec4f(mix(c0.rgb,p.b.xyz*p.b.w,k),c0.a); }"""),
    "rain": ([("gain", .25), ("density", 1), ("speed", 1), ("slant", .06), ("length", .05)], F + """
      var a=0.; for(var l=0;l<3;l++){ let fl=f32(l); let sc=(60.+fl*45.)*p.a.y; let x=(uv.x*asp+uv.y*p.a.w)*sc; let i=floor(x); let sp=(.9+h2(vec2f(i,fl))*.5)*p.a.z*(.055+.02*fl); let y=uv.y+h2(vec2f(i+3.,fl*7.))*9.-fr*sp; let f=fract(y*(1.2+fl*.5)); let on=step(.72,h2(vec2f(i+11.,floor(y*(1.2+fl*.5))+fl)));
       a+=on*smoothstep(0.,.5,f)*smoothstep(p.b.x*9.,0.,abs(f-.6)*2.)*smoothstep(.5,.1,abs(fract(x)-.5))*(.6-.15*fl); } return vec4f(c0.rgb+vec3f(.8,.88,1.)*a*p.a.x*(.25+lum(c0.rgb)),c0.a); }"""),
    "key": ([("r", .05), ("g", .6), ("b", .1), ("tolerance", .25), ("soft", .15), ("spill", .5)], F + """
      let k=p.a.xyz/max(lum(p.a.xyz),.001); let c=c0.rgb/max(lum(c0.rgb),.001); let d=length(c-k)*.35; let a=smoothstep(p.a.w,p.a.w+max(p.b.x,.001),d); var o=c0.rgb; let mx=max(o.r,o.b); if(p.a.y>max(p.a.x,p.a.z)){ o.g=mix(o.g,min(o.g,mx),p.b.y); } return vec4f(o,c0.a*a); }"""),
    "luma_key": ([("low", .02), ("high", .12)], F + "return vec4f(c0.rgb,c0.a*smoothstep(p.a.x,p.a.y,lum(c0.rgb))); }"),
    "corner_pin": ([("x0", 0), ("y0", 0), ("x1", 1), ("y1", 0), ("x2", 1), ("y2", 1), ("x3", 0), ("y3", 1)], F + """
      let a0=p.a.xy; let a1=p.a.zw; let a2=p.b.xy; let a3=p.b.zw; let d1=a1-a2; let d2=a3-a2; let sm=a0-a1+a2-a3; let det=d1.x*d2.y-d2.x*d1.y; if(abs(det)<1e-7){return vec4f(0.);}
      let g=(sm.x*d2.y-d2.x*sm.y)/det; let h=(d1.x*sm.y-sm.x*d1.y)/det; let A0=a1-a0+g*a1; let B0=a3-a0+h*a3; let C0=a0;
      let m=mat3x3f(vec3f(A0.x,A0.y,g),vec3f(B0.x,B0.y,h),vec3f(C0.x,C0.y,1.)); let c=m[0]; let d=m[1]; let e=m[2]; let r0=cross(d,e); let r1=cross(e,c); let r2=cross(c,d); let v=vec3f(uv,1.); let q=vec3f(dot(r0,v),dot(r1,v),dot(r2,v)); let t=q.xy/q.z;
      if(q.z*dot(r2,vec3f((a0+a1+a2+a3)*.25,1.))<=0. || any(t<vec2f(0.))||any(t>vec2f(1.))){return vec4f(0.);} return S(t); }"""),
    "tint": ([("r", 1), ("g", 1), ("b", 1), ("amount", .3)], F + "let l=lum(c0.rgb); return vec4f(mix(c0.rgb,l*p.a.xyz/max(lum(p.a.xyz),.001),p.a.w),c0.a); }"),
}
OUT = """
struct P { op:vec4f, size:vec4f, a:vec4f, b:vec4f, c:vec4f, d:vec4f };
@group(0) @binding(0) var<uniform> p:P; @group(0) @binding(1) var src:texture_2d<f32>; @group(0) @binding(2) var<storage,read_write> o:array<u32>;
fn hu(v:vec3u)->f32{ var x=v.x*1664525u+v.y*22695477u+v.z*1103515245u; x^=x>>16u; x*=0x45d9f3bu; x^=x>>16u; return f32(x)/4294967295.; }
@compute @workgroup_size(8,8) fn main(@builtin(global_invocation_id) g:vec3u){ if(f32(g.x)>=p.size.x||f32(g.y)>=p.size.y){return;}
 var c=clamp(textureLoad(src,vec2i(g.xy),0).rgb,vec3f(0.),vec3f(1.)); c=select(1.099*pow(c,vec3f(.45))-.099,4.5*c,c<vec3f(.018));
 c=clamp(c+(hu(vec3u(g.xy,u32(p.size.z)))-.5)/1023.,vec3f(0.),vec3f(1.)); let v=vec4u(vec4f(c,1.)*65535.+.5); let i=(g.y*u32(p.size.x)+g.x)*2u; o[i]=v.x|(v.y<<16u); o[i+1u]=v.z|(v.w<<16u); }"""


def to_linear(bgr_or_rgb: np.ndarray, bgr=False) -> np.ndarray:
    a = bgr_or_rgb[..., [2, 1, 0] + ([3] if bgr_or_rgb.shape[-1] == 4 else [])] if bgr else bgr_or_rgb
    m = 65535.0 if a.dtype == np.uint16 else 255.0
    lut = np.arange(int(m) + 1, dtype=np.float32) / m
    lut = np.where(lut < .081, lut / 4.5, ((lut + .099) / 1.099) ** (1 / .45)).astype(np.float32)
    out = np.empty(a.shape[:2] + (4,), np.float16)
    out[..., :3] = lut[a[..., :3]]
    out[..., 3] = a[..., 3].astype(np.float32) / m if a.shape[-1] == 4 else 1
    return out


class Compositor:
    def __init__(self, w, h):
        self.w, self.h = w, h
        ad = wgpu.gpu.request_adapter_sync(power_preference="high-performance")
        self.info = dict(ad.info)
        self.dev = ad.request_device_sync()
        self.key = f"{self.info.get('device')}|{self.info.get('backend_type')}|{self.info.get('description')}"
        self.sm = self.dev.create_sampler(mag_filter="linear", min_filter="linear", address_mode_u="clamp-to-edge", address_mode_v="clamp-to-edge")
        self.pipes, self._pool, self._ub, self._n = {}, [], [], 0
        self.white = self.upload(np.ones((2, 2, 4), np.float16))
        BU = wgpu.BufferUsage
        self.outbuf = self.dev.create_buffer(size=w * h * 8, usage=BU.STORAGE | BU.COPY_SRC)
        self.rb = self.dev.create_buffer(size=w * h * 8, usage=BU.MAP_READ | BU.COPY_DST)
        m = self.dev.create_shader_module(code=OUT)
        self.outpipe = self.dev.create_compute_pipeline(layout="auto", compute={"module": m, "entry_point": "main"})
        self.enc = None

    def upload(self, lin: np.ndarray):
        h, w = lin.shape[:2]
        T = wgpu.TextureUsage
        t = self.dev.create_texture(size=(w, h, 1), format="rgba16float", usage=T.TEXTURE_BINDING | T.COPY_DST)
        self.dev.queue.write_texture({"texture": t}, np.ascontiguousarray(lin).tobytes(), {"bytes_per_row": w * 8}, (w, h, 1))
        return t

    def upload8(self, rgb: np.ndarray):
        h, w = rgb.shape[:2]
        T = wgpu.TextureUsage
        t = self.dev.create_texture(size=(w, h, 1), format="rgba8unorm", usage=T.TEXTURE_BINDING | T.COPY_DST)
        self.update8(t, rgb)
        return t

    def update8(self, tex, rgb):
        h, w = rgb.shape[:2]
        if rgb.shape[2] == 3:
            buf = np.empty((h, w, 4), np.uint8)
            buf[..., :3], buf[..., 3] = rgb, 255
            rgb = buf
        self.dev.queue.write_texture({"texture": tex}, np.ascontiguousarray(rgb).data, {"bytes_per_row": w * 4}, (w, h, 1))

    def update(self, tex, lin):
        h, w = lin.shape[:2]
        self.dev.queue.write_texture({"texture": tex}, np.ascontiguousarray(lin).tobytes(), {"bytes_per_row": w * 8}, (w, h, 1))

    def target(self):
        if self._pool:
            return self._pool.pop()
        T = wgpu.TextureUsage
        return self.dev.create_texture(size=(self.w, self.h, 1), format="rgba16float", usage=T.TEXTURE_BINDING | T.STORAGE_BINDING | T.COPY_SRC)

    def free(self, t):
        self._pool.append(t)

    def _pipe(self, op):
        if op not in self.pipes:
            body = OPS[op][1]
            m = self.dev.create_shader_module(code=HEAD + body + TAIL)
            e = [{"binding": 0, "visibility": wgpu.ShaderStage.COMPUTE, "buffer": {"type": "uniform"}}, {"binding": 1, "visibility": wgpu.ShaderStage.COMPUTE, "texture": {"sample_type": "float"}},
                 {"binding": 2, "visibility": wgpu.ShaderStage.COMPUTE, "sampler": {"type": "filtering"}}, {"binding": 3, "visibility": wgpu.ShaderStage.COMPUTE, "storage_texture": {"access": "write-only", "format": "rgba16float"}},
                 {"binding": 4, "visibility": wgpu.ShaderStage.COMPUTE, "texture": {"sample_type": "float"}}]
            bgl = self.dev.create_bind_group_layout(entries=e)
            self.pipes[op] = (self.dev.create_compute_pipeline(layout=self.dev.create_pipeline_layout(bind_group_layouts=[bgl]), compute={"module": m, "entry_point": "main"}), bgl)
        return self.pipes[op]

    def _uniform(self, vals, frame):
        if self._n >= len(self._ub):
            self._ub.append(self.dev.create_buffer(size=96, usage=wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST))
        b = self._ub[self._n]
        self._n += 1
        v = np.zeros(24, np.float32)
        v[4:7] = self.w, self.h, frame
        v[8:8 + len(vals)] = vals
        self.dev.queue.write_buffer(b, 0, v.tobytes())
        return b

    def begin(self):
        self._n = 0
        self.enc = self.dev.create_command_encoder()

    def run(self, op, src, params: dict, frame, aux=None):
        names = OPS[op][0]
        unknown = set(params) - {n for n, _ in names}
        if unknown:
            raise ValueError(f"{op}: unknown parameters {sorted(unknown)}")
        vals = [float(params.get(n, d)) for n, d in names]
        pipe, bgl = self._pipe(op)
        dst = self.target()
        ub = self._uniform(vals, frame)
        bg = self.dev.create_bind_group(layout=bgl, entries=[{"binding": 0, "resource": {"buffer": ub, "offset": 0, "size": 96}}, {"binding": 1, "resource": src.create_view()}, {"binding": 2, "resource": self.sm},
                                                              {"binding": 3, "resource": dst.create_view()}, {"binding": 4, "resource": (aux or self.white).create_view()}])
        ps = self.enc.begin_compute_pass()
        ps.set_pipeline(pipe)
        ps.set_bind_group(0, bg)
        ps.dispatch_workgroups((self.w + 7) // 8, (self.h + 7) // 8)
        ps.end()
        return dst

    def finish(self, src, frame) -> bytes:
        ub = self._uniform([], frame)
        bg = self.dev.create_bind_group(layout=self.outpipe.get_bind_group_layout(0), entries=[{"binding": 0, "resource": {"buffer": ub, "offset": 0, "size": 96}}, {"binding": 1, "resource": src.create_view()},
                                                                                              {"binding": 2, "resource": {"buffer": self.outbuf, "offset": 0, "size": self.w * self.h * 8}}])
        ps = self.enc.begin_compute_pass()
        ps.set_pipeline(self.outpipe)
        ps.set_bind_group(0, bg)
        ps.dispatch_workgroups((self.w + 7) // 8, (self.h + 7) // 8)
        ps.end()
        self.enc.copy_buffer_to_buffer(self.outbuf, 0, self.rb, 0, self.w * self.h * 8)
        self.dev.queue.submit([self.enc.finish()])
        self.rb.map_sync(wgpu.MapMode.READ)
        d = bytes(self.rb.read_mapped())
        self.rb.unmap()
        self.enc = None
        return d
