// Shared GPU effects for preview and exported frames. Colors remain premultiplied.
struct VertexOutput { @builtin(position) position: vec4f, @location(0) tex_coord: vec2f }
struct EffectUniforms { resolution: vec2f, direction: vec2f, scalars: vec4f }
@group(0) @binding(0) var input_texture: texture_2d<f32>;
@group(0) @binding(1) var input_sampler: sampler;
@group(1) @binding(0) var<uniform> uniforms: EffectUniforms;
fn sample_at(uv: vec2f) -> vec4f {
    return textureSampleLevel(input_texture, input_sampler, clamp(uv, vec2f(0.0), vec2f(1.0)), 0.0);
}
fn color_at(uv: vec2f) -> vec3f {
    let c = sample_at(uv); return c.rgb / max(c.a, 0.00001);
}
fn luma(c: vec3f) -> f32 { return dot(c, vec3f(0.2126, 0.7152, 0.0722)); }
fn noise(p: vec2f) -> f32 { return fract(sin(dot(p, vec2f(12.9898,78.233))) * 43758.5453); }
@fragment
fn fragment_main(input: VertexOutput) -> @location(0) vec4f {
    let uv = input.tex_coord;
    let original = sample_at(uv);
    let rgb = original.rgb / max(original.a, 0.00001);
    let mode = i32(uniforms.scalars.x);
    let amount = clamp(uniforms.scalars.y, 0.0, 1.0);
    let detail = clamp(uniforms.scalars.z, 1.0, 100.0);
    let px = 1.0 / max(uniforms.resolution, vec2f(1.0));
    let lum = luma(rgb);
    var result = rgb;
    var alpha = original.a;
    switch mode {
        case 0: { // Cinematic teal shadows, warm highlights.
            result = (rgb - 0.5) * 1.15 + 0.5;
            result += mix(vec3f(-0.035,0.035,0.075), vec3f(0.065,0.025,-0.03), lum);
        }
        case 1: { result = rgb + vec3f(0.12,0.025,-0.08); }
        case 2: { result = rgb + vec3f(-0.08,0.02,0.12); }
        case 3: { result = mix(vec3f(lum), rgb, 0.65) * vec3f(1.08,0.98,0.80) * 0.85 + 0.08; }
        case 4: { result = vec3f(dot(rgb,vec3f(0.393,0.769,0.189)),dot(rgb,vec3f(0.349,0.686,0.168)),dot(rgb,vec3f(0.272,0.534,0.131))); }
        case 5: { result = vec3f(clamp((lum-0.5)*1.2+0.5,0.0,1.0)); }
        case 6: { result = (mix(vec3f(lum),rgb,1.65)-0.5)*1.08+0.5; }
        case 7: { result = rgb*0.75+0.16; }
        case 8: { result = mix(vec3f(lum),rgb,0.65)*0.82+0.18; }
        case 9: { result = rgb*vec3f(1.12,0.93,0.87)+vec3f(0.055,0.015,0.025); }
        case 10: { // Aspect-independent vignette, same placement at all resolutions.
            let d=length((uv-0.5)*vec2f(1.0,1.0));
            result=rgb*(1.0-smoothstep(0.20+detail*0.001,0.70,d)*0.85);
        }
        case 11: { result=rgb+vec3f((noise(floor(uv*uniforms.resolution/(1.0+detail*0.04)))-0.5)*0.25); }
        case 12: { let offset=vec2f((1.0+detail*0.16)*px.x,0.0);result=vec3f(color_at(uv+offset).r,rgb.g,color_at(uv-offset).b); }
        case 13: { let size=1.0+detail*0.75;let grid=uniforms.resolution/size;let cell=(floor(uv*grid)+0.5)/grid;result=color_at(cell); }
        case 14: { let d=px*(1.0+detail*0.02);let neighbors=(color_at(uv+vec2f(d.x,0.0))+color_at(uv-vec2f(d.x,0.0))+color_at(uv+vec2f(0.0,d.y))+color_at(uv-vec2f(0.0,d.y)))*0.25;result=rgb+(rgb-neighbors)*2.0; }
        case 15: { let d=px*(1.0+detail*0.04);let delta=luma(color_at(uv+d))-luma(color_at(uv-d));result=vec3f(0.5+delta*2.5); }
        case 16: { let levels=2.0+floor(detail/10.0);result=floor(rgb*(levels-1.0)+0.5)/(levels-1.0); }
        case 17: { let d=px*(1.0+detail*0.04);let gx=luma(color_at(uv+vec2f(d.x,0.0)))-luma(color_at(uv-vec2f(d.x,0.0)));let gy=luma(color_at(uv+vec2f(0.0,d.y)))-luma(color_at(uv-vec2f(0.0,d.y)));result=vec3f(1.0-clamp(length(vec2f(gx,gy))*4.0,0.0,1.0)); }
        case 18: { let d=px*(1.0+detail*0.04);let edge=length(color_at(uv+d)-color_at(uv-d));result=vec3f(edge*0.3,edge*1.8,edge*2.8)+rgb*0.08; }
        case 19: { result=select(rgb,1.0-rgb,rgb>vec3f(0.5)); }
        case 20: { result=1.0-rgb; }
        case 21: { let grid=uniforms.resolution/(3.0+detail*0.3);let center=(floor(uv*grid)+0.5)/grid;let tone=luma(color_at(center));let dot_size=sqrt(1.0-clamp(tone,0.0,1.0))*0.55;let radius=length(fract(uv*grid)-0.5);result=vec3f(smoothstep(dot_size-0.045,dot_size+0.045,radius)); }
        case 22: { let mirrored=sample_at(vec2f(1.0-uv.x,uv.y)); result=mirrored.rgb/max(mirrored.a,0.00001);alpha=mix(alpha,mirrored.a,amount); }
        case 23: { let p=(uv-0.5)*2.0;let warped=0.5+p*(1.0+dot(p,p)*(0.1+detail*0.004))*0.5;let c=sample_at(warped);result=c.rgb/max(c.a,0.00001);alpha=mix(alpha,c.a,amount); }
        case 24: { let d=px*(2.0+detail*0.25);let soft=(color_at(uv+d)+color_at(uv-d)+color_at(uv+vec2f(d.x,-d.y))+color_at(uv+vec2f(-d.x,d.y)))*0.25;result=1.0-(1.0-rgb)*(1.0-soft*0.55); }
        case 25: { let line=step(0.5,fract(uv.y*uniforms.resolution.y/(2.0+detail*0.10)));result=rgb*(0.65+line*0.35); }
        case 26: { result=mix(vec3f(0.08,0.02,0.23),vec3f(1.0,0.72,0.40),lum); }
        default: {}
    }
    return vec4f(clamp(mix(rgb,result,amount),vec3f(0.0),vec3f(1.0))*alpha,alpha);
}
