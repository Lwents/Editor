struct VertexOutput { @builtin(position) position: vec4f, @location(0) tex_coord: vec2f }
struct EffectUniforms { resolution: vec2f, direction: vec2f, scalars: vec4f }
@group(0) @binding(0) var input_texture: texture_2d<f32>;
@group(0) @binding(1) var input_sampler: sampler;
@group(1) @binding(0) var<uniform> uniforms: EffectUniforms;
@fragment
fn fragment_main(input: VertexOutput) -> @location(0) vec4f {
    let source = textureSample(input_texture, input_sampler, input.tex_coord);
    // Work in straight color and preserve premultiplied alpha for the compositor.
    let alpha = source.a;
    var rgb = source.rgb / max(alpha, 0.00001);
    rgb = rgb + vec3f(uniforms.scalars.x);
    rgb = (rgb - vec3f(0.5)) * uniforms.scalars.y + vec3f(0.5);
    let luminance = dot(rgb, vec3f(0.2126, 0.7152, 0.0722));
    rgb = mix(vec3f(luminance), rgb, uniforms.scalars.z);
    rgb = rgb + vec3f(uniforms.direction.x, uniforms.direction.y, -uniforms.direction.x);
    return vec4f(clamp(rgb, vec3f(0.0), vec3f(1.0)) * alpha, alpha);
}
