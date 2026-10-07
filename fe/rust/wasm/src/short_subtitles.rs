use wasm_bindgen::prelude::*;
#[wasm_bindgen(js_name = splitShortSubtitles)]
pub fn split_short_subtitles(options: JsValue) -> Result<JsValue, JsValue> {
    let input = serde_wasm_bindgen::from_value::<time::ShortSubtitleOptions>(options)
        .map_err(|e| JsValue::from_str(&e.to_string()))?;
    serde_wasm_bindgen::to_value(&time::short_subtitles(input))
        .map_err(|e| JsValue::from_str(&e.to_string()))
}
