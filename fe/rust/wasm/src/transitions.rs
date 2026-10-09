use wasm_bindgen::prelude::*;
#[wasm_bindgen(js_name = transitionDuration)]
pub fn wasm_transition_duration(requested: i64, from_duration: i64, to_duration: i64) -> i64 {
    time::transition_duration(requested, from_duration, to_duration)
}
#[wasm_bindgen(js_name = transitionProgress)]
pub fn wasm_transition_progress(local_time: i64, duration: i64) -> f64 {
    time::transition_progress(local_time, duration)
}
