use wasm_bindgen::prelude::*;
#[wasm_bindgen(js_name = exportSubtitles)]
pub fn export_subtitle_file(options: JsValue) -> Result<JsValue, JsValue> {
    let options = serde_wasm_bindgen::from_value::<time::SubtitleExportOptions>(options)
        .map_err(|e| JsValue::from_str(&e.to_string()))?;
    let file = time::export_subtitles(options).map_err(|e| JsValue::from_str(&e))?;
    serde_wasm_bindgen::to_value(&file).map_err(|e| JsValue::from_str(&e.to_string()))
}
