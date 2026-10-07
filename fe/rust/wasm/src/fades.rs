use time::fade_points;
use wasm_bindgen::prelude::*;

#[wasm_bindgen(js_name = buildFadeKeyframes)]
pub fn build_fade_keyframes(
    duration: i64,
    fade_duration: i64,
    fade_in: bool,
    fade_out: bool,
    opacity: f64,
) -> JsValue {
    serde_wasm_bindgen::to_value(&fade_points(
        duration,
        fade_duration,
        fade_in,
        fade_out,
        opacity,
    ))
    .expect("serializable fade points")
}
